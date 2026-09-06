"""Configuration dataclasses — no pydantic, no attrs."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from hushclaw.exceptions import ConfigError
from hushclaw.prompts import build_system_prompt

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 360
# Streaming is latency-sensitive. Keep the existing completion timeout for
# long non-streaming requests, but never let an idle SSE connection block a
# chat turn for several minutes.
STREAM_PROVIDER_TIMEOUT_SECONDS = 60
# Agent-loop safety defaults. These are deliberately separate from provider
# request timeouts: a healthy model call must not turn into an unbounded run.
DEFAULT_MAX_TOOL_ROUNDS = 20
DEFAULT_MAX_TOOL_CALLS = 40
DEFAULT_MAX_RUN_SECONDS = 900


def _check_fraction(name: str, val: float) -> None:
    """Raise ConfigError if val is not in [0.0, 1.0]."""
    if not (0.0 <= val <= 1.0):
        raise ConfigError(f"{name} must be in [0.0, 1.0], got {val}")


@dataclass
class AgentDefinition:
    name: str
    description: str = ""
    model: str = ""          # empty = inherit global agent.model
    system_prompt: str = ""  # empty = inherit global agent.system_prompt
    tools: list[str] = field(default_factory=list)  # empty = use global tools.enabled
    # Domain-neutral labels for routing and discovery. Business concepts such as
    # employee role, team, and reporting lines belong to solution layers.
    routing_tags: list[str] = field(default_factory=list)


@dataclass
class GatewayConfig:
    agents: list[AgentDefinition] = field(default_factory=list)
    shared_memory: bool = True
    max_concurrent_per_agent: int = 10
    pipelines: dict[str, list[str]] = field(default_factory=dict)  # name → [agent, agent, ...]
    session_ttl_hours: int = 24  # AgentLoop sessions older than this are GC'd
    # Scheduler session strategy: "job" = stable per task, "run" = new session each run
    scheduled_session_mode: str = "job"
    # Session list shaping defaults for WebUI
    session_list_limit: int = 200
    session_list_idle_days: int = 0
    session_list_hide_scheduled: bool = False
    # Lightweight Work Task worker. Disabled by default; WebUI/manual Run still works.
    work_task_worker_enabled: bool = False
    work_task_worker_interval_seconds: int = 30
    work_task_worker_max_concurrent: int = 1


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    max_connections: int = 100
    heartbeat_interval: int = 30
    api_key: str = ""           # non-empty = require X-API-Key header
    upload_dir: Path | None = None   # None = data_dir/uploads (resolved in load_config)
    max_upload_mb: int = 20          # max file size for PUT /upload
    # Optional public base URL used to compose absolute download links.
    # Example: "https://example.com". Empty means return relative /files/... URLs only.
    public_base_url: str = ""


@dataclass
class UpdateConfig:
    auto_check_enabled: bool = True
    check_interval_hours: int = 24
    channel: str = "stable"   # "stable" | "prerelease"
    # Runtime hint persisted by UI; server may still keep in-memory state.
    last_checked_at: int = 0
    check_timeout_seconds: int = 8
    cache_ttl_seconds: int = 900
    upgrade_timeout_seconds: int = 900


@dataclass
class AgentConfig:
    model: str = "claude-sonnet-4-6"
    # Auxiliary model for titles, profile extraction, reflections, compaction,
    # and other secondary work. The primary agent loop always uses model.
    cheap_model: str = ""
    max_tokens: int = 16384
    context_window: int = 180000
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS
    # WebSocket response streaming policy:
    # - final_only (default): stream every provider call via stream_complete when available,
    #   with automatic fallback to complete() on error.
    # - always: identical to final_only (kept for backwards compat).
    # - off: never use provider streaming; yields full responses as single chunks.
    stream_mode: str = "final_only"
    system_prompt: str = field(default_factory=build_system_prompt)
    # Static instructions injected into the stable (cacheable) prefix.
    # Empty = read from workspace AGENTS.md (preferred).
    # Non-empty = used as-is (overrides AGENTS.md when both exist is NOT the case;
    # AGENTS.md takes precedence — see context/engine.py DefaultContextEngine.assemble).
    instructions: str = ""
    # When True, injects a hint into the stable system prompt encouraging the model to
    # output self-contained ```html blocks for charts, visualizations, and structured UI.
    # The WebUI renders these blocks live in the HTML preview panel.
    html_render_hint: bool = True
    # Memory scope for this agent. Empty = global (unscoped) recall.
    # Set automatically to the agent's name in multi-agent (Gateway) deployments.
    # E.g. "researcher" → saves/recalls "agent:researcher" scope + "global" scope.
    memory_scope: str = ""
    # Optional workspace directory. When set (or auto-detected as .hushclaw/ in cwd):
    #   AGENTS.md → injected into stable prefix (agent behavior rules; overrides instructions)
    #   SOUL.md   → injected into stable prefix (agent identity / project persona)
    #   USER.md   → injected into dynamic suffix (user notes)
    #   skills/   → highest-priority skill tier (overrides system + user skills)
    workspace_dir: Path | None = None
    # Optional directory for trajectory JSONL files (one file per session).
    # Each turn appends a record: {turn, role, content, tool_calls, tokens, ts}.
    # Empty = disabled.
    trajectory_dir: Path | None = None
    # Per-session tool allowlist. None = inherit global tools.enabled (no restriction).
    # Non-empty list = only these tool names are permitted in this agent's sessions.
    allowed_tools: list[str] | None = field(default=None)

    def __post_init__(self):
        valid_stream_modes = {"final_only", "always", "off"}
        if self.stream_mode not in valid_stream_modes:
            raise ConfigError(
                f"agent.stream_mode must be one of {sorted(valid_stream_modes)}, "
                f"got {self.stream_mode!r}"
            )


@dataclass
class ProviderConfig:
    name: str = "anthropic-raw"
    api_key: str = ""
    # Credential pool for rotation on 429 / rate-limit errors.
    # When non-empty, the loop cycles through these keys before falling back to
    # exponential back-off. Strategy: fill_first (exhaust one key, then rotate).
    # api_key is always tried first; api_keys extends the pool.
    api_keys: list[str] = field(default_factory=list)
    base_url: str | None = None
    timeout: int = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    max_retries: int = 3          # Retry count on transient errors (0 = no retry)
    retry_base_delay: float = 1.0  # Base delay in seconds for exponential backoff
    # Token pricing in USD per 1,000 tokens (0.0 = not configured, no cost display)
    cost_per_1k_input_tokens: float = 0.0
    cost_per_1k_output_tokens: float = 0.0

    def __post_init__(self):
        if self.max_retries < 0:
            raise ConfigError(f"max_retries must be >= 0, got {self.max_retries}")

    @property
    def credential_pool(self) -> list[str]:
        """Deduplicated ordered pool: primary key first, then extras."""
        seen: set[str] = set()
        pool: list[str] = []
        for key in [self.api_key] + list(self.api_keys):
            if key and key not in seen:
                seen.add(key)
                pool.append(key)
        return pool


@dataclass
class MemoryConfig:
    data_dir: Path | None = None
    # auto: follow an existing file; new databases remain plaintext unless the
    # installer or `hushclaw database encrypt` enables SQLCipher explicitly.
    database_encryption: str = "auto"  # auto | off | sqlcipher
    max_recall_results: int = 5
    embed_provider: str = "local"  # local | ollama | openai | anthropic
    embed_model: str = ""          # e.g. "shaw/dmeta-embedding-zh", "bge-m3"; empty = provider default
    fts_weight: float = 0.6        # Hybrid search: BM25 weight
    vec_weight: float = 0.4        # Hybrid search: cosine similarity weight

    def __post_init__(self):
        if self.database_encryption not in {"auto", "off", "sqlcipher"}:
            raise ConfigError(
                "memory.database_encryption must be one of: auto, off, sqlcipher"
            )
        _check_fraction("fts_weight", self.fts_weight)
        _check_fraction("vec_weight", self.vec_weight)
        if not (0.95 <= self.fts_weight + self.vec_weight <= 1.05):
            raise ConfigError(
                f"fts_weight + vec_weight must sum to ~1.0, "
                f"got {self.fts_weight + self.vec_weight:.3f}"
            )


@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=lambda: [
        "remember", "recall", "search_notes", "get_time", "platform_info",
        "search_files", "read_file", "write_file", "edit_document",
        "list_dir", "make_download_url", "make_download_bundle", "read_artifact",
        "inspect_html_artifact", "publish_html_artifact",
        "run_shell",   # shell command execution (has _confirm_fn guard in REPL)
        "remember_skill", "search_skills", "list_skills", "use_skill", "skill_view", "inspect_skill_source", "install_skill", "evolve_skill",
        # Session-stable long-tail tool discovery bridge.
        "tool_search", "tool_call",
        "update_global_state",
        "schedule_task", "list_scheduled_tasks", "cancel_scheduled_task",
        "add_todo", "list_todos", "complete_todo",
        # Local calendar (SQLite-backed; no external deps)
        "add_calendar_event", "list_calendar_events", "update_calendar_event", "delete_calendar_event",
        "get_day_agenda", "find_free_slots", "check_time_conflicts",
        # Web fetching (lightweight, no browser required)
        "web_search",  # Public web search via Jina Search; discover URLs before reading them
        "fetch_url",   # browser-like headers + cookie jar + gzip + retry
        "jina_read",   # Jina Reader: JS-rendered clean markdown from any URL
        "research_web", "search_batch", "read_batch",
        # App connectors (registered only when configured/enabled)
        "github_search", "github_read",
        "reddit_search", "reddit_read", "reddit_post", "reddit_comment",
        "x_search", "x_read_post", "x_post", "x_reply",
        # Multi-agent collaboration (always registered via enable_agent_tools; listed here for visibility)
        "delegate_to_agent", "broadcast_to_agents",
        "list_agents", "create_agent", "update_agent", "delete_agent", "spawn_agent",
        # Browser tools (active when browser.enabled = true)
        "browser_navigate", "browser_get_content", "browser_click",
        "browser_fill", "browser_submit", "browser_screenshot",
        "browser_evaluate", "browser_close",
        "browser_open_for_user", "browser_wait_for_user",
        # Accessibility snapshot (token-efficient element interaction)
        "browser_snapshot", "browser_click_ref", "browser_fill_ref",
        # Multi-tab management
        "browser_new_tab", "browser_list_tabs", "browser_focus_tab", "browser_close_tab",
        # Remote Chrome (user's already-logged-in browser)
        "browser_connect_user_chrome",
    ])
    plugin_dir: Path | None = None
    skill_dir: Path | None = None           # system: synced by install.sh
    user_skill_dir: Path | None = None      # user-configured custom skills
    timeout: int = 30
    # Skill auto-evolution: cap on auto-created SKILL.md files
    auto_skill_cap: int = 30
    # Minimum recall_count before a memory skill becomes a promotion candidate
    auto_skill_promote_threshold: int = 5
    # Tool access profile: preset subset of tools. Applied before the enabled list.
    # "" = no preset (use enabled list only); "full" | "coding" | "messaging" | "minimal"
    profile: str = ""
    # Provider-facing schema policy. ``auto`` keeps the full surface while it
    # fits the budget, otherwise exposes common tools plus tool_search/tool_call.
    # The resolved schema JSON is frozen per session so prompt caching remains
    # stable across turns, loop recreation, and process restarts.
    discovery_mode: str = "auto"  # auto | all | bridge
    schema_budget_tokens: int = 6_000
    eager_tools: list[str] = field(default_factory=list)

    def __post_init__(self):
        valid_modes = {"auto", "all", "bridge"}
        if self.discovery_mode not in valid_modes:
            raise ConfigError(
                f"tools.discovery_mode must be one of {sorted(valid_modes)}, "
                f"got {self.discovery_mode!r}"
            )
        if self.schema_budget_tokens < 256:
            raise ConfigError(
                f"tools.schema_budget_tokens must be >= 256, got {self.schema_budget_tokens}"
            )


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "text"


@dataclass
class ContextPolicyConfig:
    """Token budget configuration for the ContextEngine."""
    stable_budget: int = 4_000
    dynamic_budget: int = 4_000
    history_budget: int = 140_000
    compact_threshold: float = 0.9
    compact_keep_turns: int = 16
    compact_strategy: str = "lossless"   # "lossless" | "summarize" | "abstractive" | "prune_tool_results"
    # Memory retrieval — raised defaults: 4000 tokens gives ~6–12 meaningful memories
    # and ensures recalled archived context is not truncated for long sessions.
    memory_min_score: float = 0.18
    memory_max_tokens: int = 4_000
    # Session recall searches prior conversation/task history. It is separate
    # from long-term semantic memory and should stay compact.
    session_recall_max_tokens: int = 800
    session_recall_limit: int = 4
    session_recall_min_query_chars: int = 12
    # Regex-based auto memory extraction in after_turn() (zero LLM calls)
    auto_extract: bool = True
    # Creativity engine: controlled forgetting + random recall
    # Exponential decay rate λ; score × e^(-λ × age_days). 0.0 = no decay.
    # 0.03 ≈ half-life 23 days; 0.1 ≈ half-life 7 days.
    memory_decay_rate: float = 0.0
    # Retrieval temperature. 0.0 = deterministic top-k; >0 = softmax-weighted random sampling.
    retrieval_temperature: float = 0.0
    # Fraction of memory_max_tokens to fill with random "serendipitous" memories. 0.0 = disabled.
    serendipity_budget: float = 0.0
    # Drop notes older than N days from recall pool. 0 = no hard cutoff.
    # Works alongside memory_decay_rate: decay softens scores, max_age_days is a hard gate.
    max_age_days: int = 0

    def __post_init__(self):
        _check_fraction("compact_threshold", self.compact_threshold)
        _check_fraction("memory_min_score", self.memory_min_score)
        _check_fraction("memory_decay_rate", self.memory_decay_rate)
        _check_fraction("retrieval_temperature", self.retrieval_temperature)
        _check_fraction("serendipity_budget", self.serendipity_budget)
        _valid_strategies = {"lossless", "summarize", "abstractive", "prune_tool_results"}
        if self.compact_strategy not in _valid_strategies:
            raise ConfigError(
                f"compact_strategy must be one of {sorted(_valid_strategies)}, "
                f"got {self.compact_strategy!r}"
            )


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[int] = field(default_factory=list)        # empty = everyone (DM)
    group_allowlist: list[int] = field(default_factory=list)  # empty = everyone (groups)
    group_policy: str = "allowlist"   # "open" | "allowlist" | "disabled"
    require_mention: bool = False     # require @bot_name in group messages
    polling_timeout: int = 30         # getUpdates long-poll timeout (seconds)
    stream: bool = True               # edit the reply progressively while text arrives
    render_mode: str = "telegram_html"  # telegram_html | plain


@dataclass
class FeishuConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # empty = everyone
    stream: bool = False              # interactive-card style updates when supported
    render_mode: str = "feishu_post"  # feishu_post | plain
    encrypt_key: str = ""       # optional: message encryption key from developer console
    verification_token: str = ""  # optional: verification token from developer console


@dataclass
class DiscordConfig:
    enabled: bool = False
    bot_token: str = ""           # Bot token from Discord Developer Portal
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[int] = field(default_factory=list)        # user IDs; empty = everyone
    guild_allowlist: list[int] = field(default_factory=list)  # server IDs; empty = all guilds
    require_mention: bool = True  # require @bot_name in guild (server) channels
    stream: bool = True           # True = edit message progressively
    render_mode: str = "discord_markdown"  # discord_markdown | plain


@dataclass
class SlackConfig:
    enabled: bool = False
    bot_token: str = ""   # xoxb-… from OAuth & Permissions
    app_token: str = ""   # xapp-… from App-Level Tokens (Socket Mode)
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # channel IDs; empty = all channels
    stream: bool = True
    render_mode: str = "slack_mrkdwn"  # slack_mrkdwn | plain


@dataclass
class DingTalkConfig:
    enabled: bool = False
    client_id: str = ""      # App Key (AppKey in DingTalk Open Platform)
    client_secret: str = ""  # App Secret
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # user open IDs; empty = everyone
    stream: bool = True   # stream mode uses WebSocket — no public endpoint needed
    render_mode: str = "sample_markdown"  # sample_markdown | plain


@dataclass
class WeChatWorkConfig:
    enabled: bool = False
    corp_id: str = ""          # Enterprise CorpID
    corp_secret: str = ""      # App Secret
    agent_id: int = 0          # App AgentID
    token: str = ""            # Callback token (for webhook verification)
    encoding_aes_key: str = "" # Optional AES key for message encryption
    agent: str = "default"
    workspace: str = ""               # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # user IDs; empty = everyone
    stream: bool = False       # WeCom does not support streaming edits
    render_mode: str = "wecom_markdown"  # wecom_markdown | plain


@dataclass
class WhatsAppConfig:
    enabled: bool = False
    account_sid: str = ""      # Twilio Account SID
    auth_token: str = ""       # Twilio Auth Token
    from_number: str = ""      # e.g. whatsapp:+14155238886
    agent: str = "default"
    workspace: str = ""        # named workspace to use for inbound messages (empty = default)
    allowlist: list[str] = field(default_factory=list)  # sender numbers; empty = everyone
    stream: bool = False       # Twilio WhatsApp messages are send-only; no edit-in-place
    render_mode: str = "plain"  # plain


@dataclass
class BrowserConfig:
    enabled: bool = True   # False = skip browser tool registration entirely
    headless: bool = True
    timeout: int = 30   # per-operation timeout in seconds
    persist_cookies: bool = True   # save/load storage state (cookies + localStorage) across sessions
    # Connect to user's running Chrome instead of launching a new Chromium instance.
    # Start Chrome with: chrome --remote-debugging-port=9222 --user-data-dir=<path>
    # Then set this to "http://localhost:9222". Empty = use managed Chromium.
    remote_debugging_url: str = ""


@dataclass
class EmailConfig:
    label: str = ""           # Display name, e.g. "Work", "Personal"
    enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""        # App password (not account password)
    mailbox: str = "INBOX"
    use_ssl: bool = True      # IMAP over SSL
    use_tls: bool = True      # SMTP STARTTLS


@dataclass
class CalendarConfig:
    label: str = ""           # Display name, e.g. "Work", "Personal"
    enabled: bool = False
    url: str = ""             # CalDAV service URL
    username: str = ""
    password: str = ""        # App password
    calendar_name: str = ""   # empty = all calendars
    sync_interval_minutes: int = 30  # background CalDAV pull interval
    timezone: str = ""        # IANA timezone, e.g. "Asia/Shanghai". Empty = follow browser.


@dataclass
class ConnectorsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)
    wecom: WeChatWorkConfig = field(default_factory=WeChatWorkConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)


@dataclass
class GitHubAppConnectorConfig:
    enabled: bool = False
    auth_mode: str = "managed"  # managed | custom | public_client
    auth_type: str = "pat"
    client_id_ref: str = "app_connectors.github.client_id"
    client_secret_ref: str = "app_connectors.github.client_secret"
    token_ref: str = "app_connectors.github.token"
    default_repo: str = ""  # owner/repo
    allow_actions: bool = False


@dataclass
class GoogleWorkspaceAppConnectorConfig:
    enabled: bool = False
    auth_mode: str = "managed"  # managed | custom | public_client
    auth_type: str = "oauth"
    client_id_ref: str = "app_connectors.google_workspace.client_id"
    client_secret_ref: str = "app_connectors.google_workspace.client_secret"
    access_token_ref: str = "app_connectors.google_workspace.access_token"
    refresh_token_ref: str = "app_connectors.google_workspace.refresh_token"
    scopes: list[str] = field(default_factory=lambda: [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    ])
    allow_actions: bool = False


@dataclass
class NotionAppConnectorConfig:
    enabled: bool = False
    auth_mode: str = "managed"  # managed | custom | public_client
    auth_type: str = "internal_token"  # internal_token | oauth
    client_id_ref: str = "app_connectors.notion.client_id"
    client_secret_ref: str = "app_connectors.notion.client_secret"
    token_ref: str = "app_connectors.notion.token"
    workspace_name: str = ""
    allow_actions: bool = False


@dataclass
class JiraAppConnectorConfig:
    enabled: bool = False
    auth_mode: str = "managed"  # managed | custom | public_client
    auth_type: str = "api_token"  # api_token | oauth
    site_url: str = ""
    email: str = ""
    client_id_ref: str = "app_connectors.jira.client_id"
    client_secret_ref: str = "app_connectors.jira.client_secret"
    token_ref: str = "app_connectors.jira.token"
    access_token_ref: str = "app_connectors.jira.access_token"
    refresh_token_ref: str = "app_connectors.jira.refresh_token"
    cloud_id: str = ""
    scopes: list[str] = field(default_factory=lambda: [
        "read:jira-work",
        "read:jira-user",
        "offline_access",
    ])
    allow_actions: bool = False


@dataclass
class RedditAppConnectorConfig:
    enabled: bool = False
    auth_mode: str = "custom"  # custom OAuth access token
    auth_type: str = "oauth"
    client_id_ref: str = "app_connectors.reddit.client_id"
    client_secret_ref: str = "app_connectors.reddit.client_secret"
    access_token_ref: str = "app_connectors.reddit.access_token"
    refresh_token_ref: str = "app_connectors.reddit.refresh_token"
    user_agent: str = "HushClaw-AppConnector/1.0"
    default_subreddit: str = ""
    allow_actions: bool = False


@dataclass
class InboundAutomationRuleConfig:
    name: str = ""
    enabled: bool = True
    connector_id: str = ""
    event_types: list[str] = field(default_factory=list)
    rule_tags: list[str] = field(default_factory=list)
    author_allowlist: list[str] = field(default_factory=list)
    author_denylist: list[str] = field(default_factory=list)
    thread_ids: list[str] = field(default_factory=list)
    action: str = "auto_reply"  # ignore | queue_only | draft_only | auto_reply
    agent: str = ""
    prompt_template: str = ""
    cooldown_seconds: int = 3600
    cooldown_scope: str = "thread_author"  # author | thread | thread_author | global
    require_allow_actions: bool = True


@dataclass
class InboundAutomationConfig:
    enabled: bool = False
    poll_interval_seconds: int = 15
    batch_size: int = 10
    default_agent: str = "default"
    default_action: str = "queue_only"
    max_reply_chars: int = 280
    rules: list[InboundAutomationRuleConfig] = field(default_factory=list)


@dataclass
class XAppConnectorConfig:
    enabled: bool = False
    auth_mode: str = "custom"  # custom = local OAuth 2.0 PKCE; managed = broker
    auth_type: str = "app_keys"  # app_keys | oauth2_user
    consumer_key_ref: str = "app_connectors.x.consumer_key"
    consumer_secret_ref: str = "app_connectors.x.consumer_secret"
    oauth_client_id_ref: str = "app_connectors.x.oauth_client_id"
    oauth_client_secret_ref: str = "app_connectors.x.oauth_client_secret"
    bearer_token_ref: str = "app_connectors.x.bearer_token"
    access_token_ref: str = "app_connectors.x.access_token"
    refresh_token_ref: str = "app_connectors.x.refresh_token"
    scopes: list[str] = field(default_factory=lambda: [
        "tweet.read",
        "tweet.write",
        "users.read",
        "offline.access",
    ])
    stream_enabled: bool = False
    stream_rules: list[dict] = field(default_factory=list)
    allow_actions: bool = False


@dataclass
class AppConnectorsConfig:
    broker_base_url: str = "https://bus-ie.aibotplatform.com/hushclaw/app-connectors/oauth"
    github: GitHubAppConnectorConfig = field(default_factory=GitHubAppConnectorConfig)
    google_workspace: GoogleWorkspaceAppConnectorConfig = field(default_factory=GoogleWorkspaceAppConnectorConfig)
    notion: NotionAppConnectorConfig = field(default_factory=NotionAppConnectorConfig)
    jira: JiraAppConnectorConfig = field(default_factory=JiraAppConnectorConfig)
    reddit: RedditAppConnectorConfig = field(default_factory=RedditAppConnectorConfig)
    x: XAppConnectorConfig = field(default_factory=XAppConnectorConfig)
    inbound_automation: InboundAutomationConfig = field(default_factory=InboundAutomationConfig)


@dataclass
class TranssionConfig:
    """Persisted Transsion / TEX AI Router auth state.

    The API key (sk-xxx) itself is stored in provider.api_key.
    This section holds the login state needed to re-acquire credentials.
    """
    email: str = ""           # email used for the last successful login
    access_token: str = ""    # JWT accessToken (used to call AcquireAPICredentials)
    display_name: str = ""    # user's display name (cosmetic only)


@dataclass
class WorkspaceEntry:
    """A named workspace entry in the workspace registry."""
    name: str
    path: str
    description: str = ""


@dataclass
class WorkspacesConfig:
    """Registry of named workspaces (multi-workspace support)."""
    list: list[WorkspaceEntry] = field(default_factory=list)


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    context: ContextPolicyConfig = field(default_factory=ContextPolicyConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    connectors: ConnectorsConfig = field(default_factory=ConnectorsConfig)
    app_connectors: AppConnectorsConfig = field(default_factory=AppConnectorsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    emails: list = field(default_factory=list)     # list[EmailConfig]
    calendars: list = field(default_factory=list)  # list[CalendarConfig]
    transsion: TranssionConfig = field(default_factory=TranssionConfig)
    workspaces: WorkspacesConfig = field(default_factory=WorkspacesConfig)
    # Free-form API keys for skills and integrations.
    # Values may be plaintext for backward compatibility, but the preferred form
    # is a stable secret reference like "secret://api_keys.jina".
    api_keys: dict = field(default_factory=dict)

    @property
    def email(self) -> EmailConfig:
        """First email account, or an empty disabled EmailConfig for backward compat."""
        return self.emails[0] if self.emails else EmailConfig()

    @property
    def calendar(self) -> CalendarConfig:
        """First calendar account, or an empty disabled CalendarConfig for backward compat."""
        return self.calendars[0] if self.calendars else CalendarConfig()
