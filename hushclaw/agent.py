"""Agent: high-level class combining provider, memory, tools, and loop."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from hushclaw.config import Config, load_config
from hushclaw.credentials import CredentialService
from hushclaw.context.engine import ContextEngine
from hushclaw.learning.controller import LearningController
from hushclaw.memory.kinds import USER_VISIBLE_MEMORY_KINDS
from hushclaw.loop import AgentLoop
from hushclaw.memory.store import MemoryStore
from hushclaw.providers.registry import get_provider
from hushclaw.runtime.hooks import HookBus
from hushclaw.runtime.services import RuntimeServices
from hushclaw.skills.prompt_blocks import build_skill_index_prompt_block
from hushclaw.tools.registry import ToolRegistry
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from hushclaw.gateway import Gateway
    from hushclaw.prompt_blocks import PromptBlockRegistry

log = get_logger("agent")


class Agent:
    """
    High-level Agent that wires together:
      - Config loading
      - LLM provider
      - Persistent memory store
      - Tool registry
      - AgentLoop (ReAct)
    """

    def __init__(
        self,
        config: Config | None = None,
        project_dir: Path | None = None,
        shared_memory: MemoryStore | None = None,
        context_engine: ContextEngine | None = None,
        prompt_blocks: "PromptBlockRegistry | None" = None,
        hook_bus: HookBus | None = None,
    ) -> None:
        self.config = config or load_config(project_dir)
        setup_logging(self.config.logging.level, self.config.logging.format)
        self.hook_bus = hook_bus or HookBus()

        if shared_memory is not None:
            self.memory = shared_memory
        else:
            self.memory = MemoryStore(
                data_dir=self.config.memory.data_dir,
                embed_provider=self.config.memory.embed_provider,
                embed_model=self.config.memory.embed_model,
                api_key=self.config.provider.api_key,
                fts_weight=self.config.memory.fts_weight,
                vec_weight=self.config.memory.vec_weight,
                database_encryption=self.config.memory.database_encryption,
            )

        self.provider = get_provider(self.config.provider)
        self.context_engine = context_engine  # None → AgentLoop uses DefaultContextEngine
        self.prompt_blocks = prompt_blocks

        self._setup_registry(self.config)
        self._learning = LearningController(
            self.memory,
            skill_manager=self._skill_manager,
            provider=self.provider,
            agent_config=self.config.agent,
            session_title_callback=getattr(self, "_session_title_update_callback", None),
        )
        self._runtime_services = RuntimeServices(
            self.memory,
            self.config,
            context_engine=self.context_engine,
            prompt_blocks=self.prompt_blocks,
        )
        # Legacy compatibility: some tests still poke these attrs directly.
        self._projection_worker = None
        self._retention_executor = None
        self._scheduler = None  # set later by HushClawServer after Scheduler is created
        self._install_runtime_hooks()

        log.info(
            "Agent ready: provider=%s model=%s tools=%d",
            self.config.provider.name,
            self.config.agent.model,
            len(self.registry),
        )

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def set_session_title_update_callback(self, callback) -> None:
        self._session_title_update_callback = callback
        if hasattr(self, "_learning") and self._learning is not None:
            self._learning.session_title_callback = callback

    def reload_runtime(self, new_config: Config) -> None:
        """Hot-reload provider/tools/skills from new config."""
        self.config = new_config
        self.provider = get_provider(new_config.provider)
        self._setup_registry(new_config)
        if hasattr(self, "_learning") and self._learning is not None:
            self._learning.skill_manager = self._skill_manager
            self._learning.provider = self.provider
            self._learning.agent_config = self.config.agent
            self._learning.session_title_callback = getattr(self, "_session_title_update_callback", None)
        else:
            self._learning = LearningController(
                self.memory,
                skill_manager=self._skill_manager,
                provider=self.provider,
                agent_config=self.config.agent,
                session_title_callback=getattr(self, "_session_title_update_callback", None),
            )
        services = self._ensure_runtime_services()
        services.set_config(self.config)
        services.set_context_engine(self.context_engine)
        services.set_prompt_blocks(self.prompt_blocks)
        self.enable_agent_tools()

    def set_prompt_blocks(self, prompt_blocks: "PromptBlockRegistry | None") -> None:
        """Install the active prompt block registry for future loops/services."""
        self.prompt_blocks = prompt_blocks
        if self.context_engine is not None:
            return
        services = self._ensure_runtime_services()
        services.set_prompt_blocks(prompt_blocks)

    def _setup_registry(self, config: Config) -> None:
        """Build ToolRegistry + SkillRegistry from config. Called by __init__ and reload_runtime."""
        from hushclaw.skills.loader import SkillRegistry
        from hushclaw.skills.loader import _BUILTINS_DIR as _SK_BUILTINS

        self.registry = ToolRegistry()
        self.registry.load_builtins(
            enabled=None,  # filter applied after all sources (builtins + plugins + skills)
            browser_enabled=config.browser.enabled,
        )
        if config.tools.plugin_dir:
            self.registry.load_plugins(config.tools.plugin_dir)

        # ── Three-tier SkillRegistry + bundled tool loading ──────────────────
        # Priority (ascending — later dirs override earlier):
        #   1. Built-ins (always loaded by SkillRegistry itself)
        #   2. system skill_dir
        #   3. user_skill_dir
        #   4. workspace .hushclaw/skills/ (highest priority)
        skill_dirs: list[tuple] = []
        skill_dir = config.tools.skill_dir
        if skill_dir:
            skill_dirs.append((skill_dir, "system"))

        user_skill_dir = config.tools.user_skill_dir
        if user_skill_dir and user_skill_dir.exists():
            skill_dirs.append((user_skill_dir, "user"))

        # Workspace skills — auto-detected from workspace_dir
        workspace_skill_dir: Path | None = None
        if config.agent.workspace_dir:
            ws_skills = config.agent.workspace_dir / "skills"
            if ws_skills.is_dir():
                skill_dirs.append((ws_skills, "workspace"))
                workspace_skill_dir = ws_skills

        if skill_dirs or _SK_BUILTINS.exists():
            self._skill_registry = SkillRegistry(skill_dirs)
            log.info(
                "Loaded %d skills from %d source(s)",
                len(self._skill_registry), len(skill_dirs),
            )
        else:
            self._skill_registry = None

        if hasattr(self, "credential_service") and self.credential_service is not None:
            self.credential_service.set_skill_registry(self._skill_registry)
        else:
            self.credential_service = CredentialService(skill_registry=self._skill_registry)

        # ── SkillManager — unified façade injected as _skill_manager ─────────
        from hushclaw.skills.installer import SkillInstaller
        from hushclaw.skills.validator import SkillValidator
        from hushclaw.skills.manager import SkillManager

        install_dir = config.tools.user_skill_dir
        self._skill_manager = SkillManager(
            registry=self._skill_registry,
            installer=SkillInstaller(),
            validator=SkillValidator(),
            install_dir=install_dir,
            tool_registry=self.registry,
            # gateway bound later via set_gateway() in new_loop()
            workspace_install_dir=workspace_skill_dir,
        )

        # Bundled tools — system skill tools (no namespace, may override builtins)
        if skill_dir and skill_dir.exists():
            for tools_dir in skill_dir.glob("*/tools"):
                if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                    self.registry.load_plugins(tools_dir)
                    log.info("Loaded bundled system tools from %s", tools_dir)

        # Bundled tools — user skill tools (namespaced to avoid collisions)
        if user_skill_dir and user_skill_dir.exists():
            for tools_dir in user_skill_dir.glob("*/tools"):
                if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                    skill_name = tools_dir.parent.name
                    self.registry.load_plugins(tools_dir, namespace=skill_name)
                    log.info("Loaded bundled user tools from %s", tools_dir)

        # Bundled tools — workspace skill tools (no namespace, highest priority)
        if workspace_skill_dir:
            for tools_dir in workspace_skill_dir.glob("*/tools"):
                if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
                    self.registry.load_plugins(tools_dir)
                    log.info("Loaded bundled workspace tools from %s", tools_dir)

        # Apply profile preset (narrows tool universe) then the enabled filter.
        self.registry.apply_profile(config.tools.profile)

        try:
            from hushclaw.app_connectors import AppConnectorRegistry

            app_registry = AppConnectorRegistry(config.app_connectors)
            for td in app_registry.enabled_tools():
                self.registry.register_definition(td)
        except Exception as exc:
            log.warning("Failed to load app connector tools: %s", exc)

        # Auto-inject email tools when email integration is configured.
        if getattr(config, "emails", None) and any(a.enabled for a in config.emails):
            _email_tools = [
                "list_emails", "read_email", "send_email", "search_emails",
                "mark_email_read", "move_email", "reply_email", "delete_email",
                "forward_email", "list_email_folders",
            ]
            _existing = set(config.tools.enabled)
            config.tools.enabled = list(config.tools.enabled) + [
                t for t in _email_tools if t not in _existing
            ]
        self.registry.apply_enabled_filter(config.tools.enabled)

    def _effective_prompt_blocks(self):
        prompt_blocks = getattr(self, "prompt_blocks", None)
        if self._skill_registry is None:
            return prompt_blocks
        skill_block = build_skill_index_prompt_block(self._skill_registry)
        if prompt_blocks is None:
            from hushclaw.prompt_blocks import build_prompt_registry

            return build_prompt_registry(
                system_prompt=self.config.agent.system_prompt,
                blocks=[skill_block],
            )
        registry = prompt_blocks.copy()
        registry.register(skill_block)
        return registry

    def enable_agent_tools(self) -> None:
        """Register agent collaboration tools (call after gateway is available)."""
        from hushclaw.tools.builtins import agent_tools
        self.registry.register_module(agent_tools)

    def new_loop(
        self,
        session_id: str | None = None,
        thread_id: str | None = None,
        gateway: "Gateway | None" = None,
        context_engine: ContextEngine | None = None,
    ) -> AgentLoop:
        """Create a fresh AgentLoop for a new or resumed session.

        If ``thread_id`` is provided, restore the loop from that thread's event
        history. Otherwise it falls back to session-scoped restore, which remains
        compatible with older turn-based data.
        """
        if self._skill_manager is not None:
            self._skill_manager.set_gateway(gateway)
        prompt_blocks = self._effective_prompt_blocks()
        services = self._ensure_runtime_services()
        services.ensure_started(context_engine or self.context_engine)
        self._projection_worker = services.projection_worker
        self._retention_executor = services.retention_executor
        loop = AgentLoop(
            config=self.config,
            provider=self.provider,
            memory=self.memory,
            registry=self.registry,
            session_id=session_id or make_id("s-"),
            gateway=gateway,
            context_engine=context_engine or self.context_engine,
            hook_bus=self.hook_bus,
            prompt_blocks=prompt_blocks,
            skill_registry=self._skill_registry,
            skill_manager=self._skill_manager,
            scheduler=self._scheduler,
        )
        # Minimal test/embedding constructions may intentionally skip the full
        # registry setup.  Tools already treat this dependency as optional, so
        # keep loop creation equally explicit and deterministic.
        loop.executor.set_context(
            _credential_service=getattr(self, "credential_service", None)
        )
        if thread_id:
            loop.restore_thread(thread_id)
        else:
            loop.restore_session(loop.session_id)
        return loop

    def _ensure_projection_worker(self, context_engine: ContextEngine | None = None) -> None:
        """Start ProjectionWorker via RuntimeServices (legacy wrapper)."""
        services = self._ensure_runtime_services()
        services.ensure_started(context_engine or self.context_engine)
        self._projection_worker = services.projection_worker

    def _ensure_retention_executor(self) -> None:
        """Start RetentionExecutor via RuntimeServices (legacy wrapper)."""
        services = self._ensure_runtime_services()
        services.ensure_started(self.context_engine)
        self._retention_executor = services.retention_executor

    def _ensure_runtime_services(self) -> RuntimeServices:
        services = getattr(self, "_runtime_services", None)
        if services is None:
            services = RuntimeServices(
                self.memory,
                self.config,
                context_engine=self.context_engine,
                projection_worker=getattr(self, "_projection_worker", None),
                retention_executor=getattr(self, "_retention_executor", None),
            )
            self._runtime_services = services
        return services

    def on_hook(self, event_name: str, handler) -> None:
        """Register a runtime lifecycle hook handler."""
        self.hook_bus.on(event_name, handler)

    def _install_runtime_hooks(self) -> None:
        """Register default lifecycle hooks that persist session metadata."""

        def _annotate(event) -> None:
            payload = event.payload
            self.memory.annotate_session(
                str(payload.get("session_id") or ""),
                source=str(payload.get("entrypoint") or ""),
                workspace=str(payload.get("workspace") or ""),
                title=self.memory._clip_text(str(payload.get("user_input") or ""), 56),
            )

        def _record_compaction(event) -> None:
            payload = event.payload
            self.memory.record_session_compaction(
                str(payload.get("session_id") or ""),
                archived=int(payload.get("archived") or 0),
                kept=int(payload.get("kept") or 0),
            )

        def _flush_working_state(event) -> None:
            payload = event.payload
            session_id = str(payload.get("session_id") or "")
            messages = list(payload.get("messages") or [])
            if not session_id or not messages:
                return
            state_text = self._build_working_state(messages)
            if state_text:
                self.memory.save_session_working_state(session_id, state_text)

        self.on_hook("pre_session_init", _annotate)
        self.on_hook("pre_session_init", self._learning.on_pre_session_init)
        self.on_hook("post_tool_call", self._learning.on_post_tool_call)
        self.on_hook("post_turn_persist", _annotate)
        self.on_hook("post_turn_persist", self._learning.on_post_turn_persist)
        self.on_hook("pre_compact", _flush_working_state)
        self.on_hook("post_compact", _record_compaction)

    @staticmethod
    def _build_working_state(messages) -> str:
        """Build a structured working-state checkpoint from recent context."""
        recent = list(messages or [])
        if not recent:
            return ""

        def _to_text(content) -> str:
            if isinstance(content, str):
                return " ".join(content.split())
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text" and block.get("text"):
                            parts.append(str(block.get("text")))
                        elif block.get("type") == "tool_use" and block.get("name"):
                            parts.append(f"[tool_use:{block.get('name')}]")
                    elif block:
                        parts.append(str(block))
                return " ".join(" ".join(parts).split())
            return " ".join(str(content).split())

        def _clip(text: str, limit: int) -> str:
            clean = " ".join((text or "").split())
            if len(clean) <= limit:
                return clean
            return clean[:limit].rstrip() + "…"

        def _sentences(text: str, limit: int = 2) -> list[str]:
            clean = " ".join((text or "").split())
            if not clean:
                return []
            chunks = []
            for piece in clean.replace("?", ".").replace("!", ".").split("."):
                part = piece.strip(" -")
                if part:
                    chunks.append(part)
                if len(chunks) >= limit:
                    break
            return chunks or [_clip(clean, 180)]

        user_msgs = [m for m in recent if getattr(m, "role", "") == "user"]
        assistant_msgs = [m for m in recent if getattr(m, "role", "") == "assistant"]
        tool_msgs = [m for m in recent if getattr(m, "role", "") == "tool"]

        active_goal = _to_text(user_msgs[-1].content) if user_msgs else ""
        previous_goal = _to_text(user_msgs[-2].content) if len(user_msgs) > 1 else ""
        last_assistant = _to_text(assistant_msgs[-1].content) if assistant_msgs else ""
        lines: list[str] = []

        if active_goal:
            lines.append("### Goal")
            lines.append(_clip(active_goal, 240))

        progress_items: list[str] = []
        if previous_goal and previous_goal != active_goal:
            progress_items.append(f"Previous user ask: {_clip(previous_goal, 180)}")
        for sentence in _sentences(last_assistant, limit=2):
            progress_items.append(f"Assistant progress: {_clip(sentence, 180)}")
        if progress_items:
            lines.append("")
            lines.append("### Progress")
            for item in progress_items[:4]:
                lines.append(f"- {item}")

        open_loops: list[str] = []
        if active_goal:
            open_loops.append(f"Respond to the latest ask: {_clip(active_goal, 180)}")
        if last_assistant and "[tool_use:" in last_assistant:
            open_loops.append("Resolve the outstanding tool-use flow before closing the task.")
        if open_loops:
            lines.append("")
            lines.append("### Open Loops")
            for item in open_loops[:3]:
                lines.append(f"- {item}")

        tool_items: list[str] = []
        for tool_msg in tool_msgs[-3:]:
            tool_name = getattr(tool_msg, "tool_name", "") or "tool"
            tool_text = _to_text(getattr(tool_msg, "content", ""))
            if tool_text:
                tool_items.append(f"{tool_name}: {_clip(tool_text, 160)}")
        if tool_items:
            lines.append("")
            lines.append("### Recent Tool Outputs")
            for item in tool_items:
                lines.append(f"- {item}")

        return "\n".join(lines).strip()

    async def chat(self, message: str, session_id: str | None = None) -> str:
        """Single-shot chat (no session persistence across calls)."""
        loop = self.new_loop(session_id)
        try:
            return await loop.run(message)
        finally:
            await loop.aclose()

    async def chat_stream(
        self, message: str, session_id: str | None = None
    ) -> AsyncIterator[str]:
        """Single-shot streaming chat."""
        loop = self.new_loop(session_id)
        try:
            async for chunk in loop.stream_run(message):
                yield chunk
        finally:
            await loop.aclose()

    def remember(
        self,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        note_type: str = "fact",
        memory_kind: str = "",
    ) -> str:
        """Directly save a memory note, bypassing LLM."""
        return self.memory.remember(
            content,
            title=title,
            tags=tags,
            note_type=note_type,
            memory_kind=memory_kind,
        )

    def search(
        self,
        query: str,
        limit: int = 5,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """Directly search memory, bypassing LLM."""
        return self.memory.search(query, limit=limit, include_kinds=include_kinds)

    def list_memories(
        self,
        limit: int = 20,
        offset: int = 0,
        tag: str | None = None,
        exclude_tags: list[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """List recent memory notes, optionally filtered by tag."""
        if tag:
            return self.memory.search_by_tag(tag, limit=limit)
        return self.memory.list_recent_notes(
            limit=limit,
            offset=offset,
            exclude_tags=exclude_tags,
            include_kinds=include_kinds if include_kinds is not None else USER_VISIBLE_MEMORY_KINDS,
        )

    def forget(self, note_id: str) -> bool:
        """Delete a memory note by its ID. Returns True if deleted."""
        return self.memory.delete_note(note_id)

    def list_sessions(self) -> list[dict]:
        return self.memory.list_sessions()

    async def aclose(self, close_memory: bool = True) -> None:
        """Async shutdown: await worker stops before closing memory.

        Guarantees that ProjectionWorker and RetentionExecutor finish any
        in-flight writes before the DB connection is released.
        """
        services = getattr(self, "_runtime_services", None)
        if services is not None:
            try:
                await services.stop()
            except Exception:
                pass
        else:
            for attr in ("_projection_worker", "_retention_executor"):
                worker = getattr(self, attr, None)
                if worker is not None:
                    try:
                        await worker.stop()
                    except Exception:
                        pass
        if close_memory:
            self.memory.close()

    def close(self, close_memory: bool = True) -> None:
        """Sync shutdown wrapper around aclose().

        Schedules aclose() on the running loop when called from async context;
        runs it synchronously otherwise.
        """
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(self.aclose(close_memory))
        except RuntimeError:
            _asyncio.run(self.aclose(close_memory))

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *_) -> None:
        self.close()
