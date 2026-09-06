"""HushClaw CLI: argparse REPL + subcommands."""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from hushclaw import __version__
from hushclaw.cli.backup import cmd_backup_export, cmd_backup_import
from hushclaw.cli.database import (
    cmd_database_encrypt,
    cmd_database_harden,
    cmd_database_recovery_key,
    cmd_database_status,
)
from hushclaw.cli.repl import repl as _run_repl
from hushclaw.cli.config import cmd_config_show, cmd_config_path, cmd_config_set
from hushclaw.cli.setup import cmd_init, cmd_doctor


# ---------------------------------------------------------------------------
# Lazy imports — keep startup time minimal
# ---------------------------------------------------------------------------

def _make_agent():
    from hushclaw.agent import Agent
    return Agent()


def _make_runtime(distro_id: str = "personal"):
    from hushclaw.distro import DistroRuntime

    return DistroRuntime(distro_id).build()


# ---------------------------------------------------------------------------
# Provider / error helpers (used by multiple subcommands)
# ---------------------------------------------------------------------------

def _provider_env_var(provider_name: str) -> str:
    """Return the canonical env-var name for the given provider."""
    n = provider_name.lower()
    if "gemini" in n or "google" in n:
        return "GEMINI_API_KEY"
    if "minimax" in n:
        return "MINIMAX_API_KEY"
    if "openai" in n:
        return "OPENAI_API_KEY"
    if "aigocode" in n:
        return "AIGOCODE_API_KEY"
    return "ANTHROPIC_API_KEY"


def _current_provider_name() -> str:
    """Best-effort: read provider name from config without raising."""
    try:
        from hushclaw.config.loader import load_config
        return load_config().provider.name
    except Exception:
        return "anthropic-raw"


def _classify_error(e: Exception, provider_name: str = "") -> str:
    msg = str(e)
    msg_l = msg.lower()
    pname = provider_name or _current_provider_name()
    env_var = _provider_env_var(pname)
    if "401" in msg or "unauthorized" in msg or "api key" in msg_l or "api_key" in msg_l:
        return f"[API Error] 401 Unauthorized — check your {env_var}\n  detail: {msg}"
    if "429" in msg or "rate limit" in msg_l:
        return f"[API Error] 429 Rate Limited — please wait before retrying\n  detail: {msg}"
    if any(c in msg for c in ("500", "502", "503")):
        return f"[API Error] Server error — the API may be temporarily unavailable\n  detail: {msg}"
    if "timeout" in msg_l or "timed out" in msg_l:
        return f"[Network Error] Request timed out — check your connection\n  detail: {msg}"
    if (
        "connection" in msg_l
        or "network" in msg_l
        or "socket" in msg_l
        or "broken pipe" in msg_l
        or "unexpected_eof_while_reading" in msg_l
        or "eof occurred in violation of protocol" in msg_l
    ):
        return f"[Network Error] Connection failed — check your internet connection\n  detail: {msg}"
    if "permission denied" in msg_l or "tool" in msg_l:
        return f"[Tool Error] {msg}"
    return f"[Error] {msg}"


def _handle_agent_init_error(e: Exception) -> None:
    """Print a helpful error message when agent initialization fails."""
    msg = str(e)
    msg_l = msg.lower()
    if "api key" in msg_l or "api_key" in msg_l or "not found" in msg_l and "key" in msg_l:
        pname = _current_provider_name()
        env_var = _provider_env_var(pname)
        print(
            f"\n[Setup Required] {pname} API key not found.\n"
            f"\n  Option 1 (recommended): hushclaw init"
            f"\n  Option 2: export {env_var}=<your-key>"
            f"\n  Option 3: add to config file — hushclaw config path\n",
            file=sys.stderr,
        )
    elif e.__class__.__name__ == "MemoryDatabaseError" or (
        "readonly database" in msg_l
        or "attempt to write a readonly database" in msg_l
        or "constraint failed" in msg_l
        or "integrityerror" in msg_l
        or "database is locked" in msg_l
        or "database disk image is malformed" in msg_l
        or "no such column" in msg_l
    ):
        cause = getattr(e, "cause", None)
        detail = str(cause or e)
        data_dir = getattr(e, "data_dir", None)
        db_path = getattr(e, "db_path", None)
        backup_path = getattr(e, "backup_path", None)
        paths = ""
        if data_dir:
            paths += f"\n    data dir: {data_dir}"
        if db_path:
            paths += f"\n    database: {db_path}"
        if backup_path:
            paths += f"\n    backup:   {backup_path}"
        print(
            "\n[Storage Error] HushClaw could not initialize its local memory database.\n"
            f"\n  Detail: {detail}"
            f"{paths}"
            "\n  Try:"
            "\n    bash ~/.hushclaw/repo/install.sh --stop"
            "\n    bash ~/.hushclaw/repo/install.sh --update"
            "\n    hushclaw doctor"
            "\n\n  If it still fails, check ownership/permissions of the data directory shown by:"
            "\n    hushclaw config path\n",
            file=sys.stderr,
        )
    else:
        print(f"Failed to initialize agent: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# REPL entry point
# ---------------------------------------------------------------------------

def _repl(agent, session_id: str | None = None) -> None:
    """Launch the interactive REPL (delegates to cli.repl)."""
    _run_repl(agent, _classify_error, session_id=session_id)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_chat(args, runtime) -> int:
    agent = runtime.agent
    gateway = runtime.gateway
    message = " ".join(args.message)
    try:
        if getattr(args, "stream", False):
            async def _stream():
                loop_obj = agent.new_loop(gateway=gateway)
                async for chunk in loop_obj.stream_run(message):
                    print(chunk, end="", flush=True)
                print()
            asyncio.run(_stream())
        else:
            loop_obj = agent.new_loop(gateway=gateway)
            response = asyncio.run(loop_obj.run(message))
            print(response)
        return 0
    except Exception as e:
        print(_classify_error(e), file=sys.stderr)
        return 1


def cmd_remember(args, runtime) -> int:
    content = " ".join(args.content)
    tags = args.tags.split(",") if args.tags else []
    nid = runtime.os_api.remember(
        content,
        metadata={"title": args.title or "", "tags": tags},
    )
    print(f"Saved: {nid[:8]}")
    return 0


def cmd_search(args, runtime) -> int:
    results = runtime.os_api.search_memory(" ".join(args.query), limit=args.limit)
    if not results:
        print("No results found.")
        return 0
    for r in results:
        print(f"\n[{r['note_id'][:8]}] {r['title']} (score={r['score']:.3f})")
        print(f"  {r['body'][:200]}")
    return 0


def cmd_sessions(args, runtime) -> int:
    sessions, _ = runtime.os_api.list_sessions(limit=200)
    if not sessions:
        print("No sessions found.")
        return 0
    for s in sessions:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_turn"]))
        itok = s.get("total_input_tokens") or 0
        otok = s.get("total_output_tokens") or 0
        print(
            f"  {s['session_id']}  turns={s['turn_count']}  last={ts}"
            f"  in={itok} out={otok}"
        )
    return 0


def cmd_sessions_resume(args, runtime) -> int:
    _repl(runtime.agent, session_id=args.session_id)
    return 0


def cmd_tools_list(args, runtime) -> int:
    for td in runtime.os_api.list_tools():
        print(f"  {td['name']:20} {td.get('description', '')}")
    return 0


def cmd_reindex_memories(args, runtime) -> int:
    import sys
    memory = runtime.agent.memory
    batch_size = args.batch_size
    rows = memory.conn.execute(
        "SELECT n.note_id, n.title, b.body FROM notes n JOIN note_bodies b ON b.note_id = n.note_id"
    ).fetchall()
    total = len(rows)
    if total == 0:
        print("No notes found.")
        return 0
    print(f"Reindexing {total} notes with model key '{memory._vec._model_key}' ...")
    done = 0
    for row in rows:
        text = (row["title"] + "\n" + row["body"]).strip()
        memory._vec.index(row["note_id"], text)
        done += 1
        if done % batch_size == 0 or done == total:
            print(f"  {done}/{total}", end="\r", flush=True)
    print(f"\nDone. Reindexed {done} notes.")
    return 0


def cmd_rebuild_beliefs(args, runtime) -> int:
    stats = runtime.os_api.rebuild_belief_models(dry_run=args.dry_run)
    mode = "dry-run" if stats.get("dry_run") else "applied"
    print(
        f"Belief Map rebuild {mode}: "
        f"{stats.get('notes_scanned', 0)} notes, "
        f"{stats.get('bucket_count', 0)} buckets, "
        f"{stats.get('moved_from_general', 0)} moved from general"
    )
    for key, count in sorted((stats.get("buckets") or {}).items()):
        print(f"  {key}: {count}")
    return 0


def cmd_serve(args, runtime) -> int:
    from hushclaw.server import HushClawServer

    agent = runtime.agent
    if hasattr(args, "host") and args.host:
        agent.config.server.host = args.host
    if hasattr(args, "port") and args.port:
        agent.config.server.port = args.port

    server = HushClawServer(runtime.gateway, agent.config.server, os_api=runtime.os_api)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nServer stopped.")
    return 0


def cmd_agents_list(args, runtime) -> int:
    for a in runtime.os_api.list_agents():
        desc = f"  {a['description']}" if a['description'] else ""
        print(f"  {a['name']}{desc}")
    return 0


def cmd_agents_run(args, runtime) -> int:
    try:
        response = asyncio.run(runtime.gateway.execute(args.agent_name, args.message))
        print(response)
    except Exception as e:
        print(_classify_error(e), file=sys.stderr)
        return 1
    return 0


def cmd_agents_pipeline(args, runtime) -> int:
    names = [n.strip() for n in args.agent_names.split(",") if n.strip()]
    try:
        response = asyncio.run(runtime.gateway.pipeline(names, args.message))
        print(response)
    except Exception as e:
        print(_classify_error(e), file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hushclaw",
        description="HushClaw — lightweight, token-first AI Agent with persistent memory",
    )
    p.add_argument("--version", action="version", version=f"hushclaw {__version__}")
    p.add_argument(
        "--provider", metavar="NAME",
        help="Override provider (anthropic-raw, anthropic-sdk, ollama, openai-raw, aigocode-raw)",
    )
    p.add_argument("--model", metavar="MODEL", help="Override model name")
    p.add_argument("--session", metavar="ID", help="Session ID to resume")
    p.add_argument("--log-level", metavar="LEVEL", default=None,
                   help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    sub = p.add_subparsers(dest="command")

    sub.add_parser("init", help="Interactive setup wizard (first-time configuration)")
    sub.add_parser("doctor", help="Check configuration and environment health")

    chat_p = sub.add_parser("chat", help="Send a single message")
    chat_p.add_argument("message", nargs="+", help="Message text")
    chat_p.add_argument("--stream", action="store_true", help="Stream output as it is generated")

    rem_p = sub.add_parser("remember", help="Save a note to memory")
    rem_p.add_argument("content", nargs="+")
    rem_p.add_argument("--title", default="")
    rem_p.add_argument("--tags", default="", help="Comma-separated tags")

    srch_p = sub.add_parser("search", help="Search memory")
    srch_p.add_argument("query", nargs="+")
    srch_p.add_argument("--limit", type=int, default=5)

    sess_p = sub.add_parser("sessions", help="List or resume sessions")
    sess_sub = sess_p.add_subparsers(dest="sessions_command")
    resume_p = sess_sub.add_parser("resume", help="Resume a session")
    resume_p.add_argument("session_id")

    tools_p = sub.add_parser("tools", help="Tool management")
    tools_sub = tools_p.add_subparsers(dest="tools_command")
    tools_sub.add_parser("list", help="List available tools")

    cfg_p = sub.add_parser("config", help="Configuration management")
    cfg_sub = cfg_p.add_subparsers(dest="config_command")
    show_p = cfg_sub.add_parser("show", help="Show current configuration")
    show_p.add_argument("--json", action="store_true", help="Output as JSON instead of TOML")
    cfg_sub.add_parser("path", help="Show config and data directory paths")
    set_p = cfg_sub.add_parser("set", help="Set a config value (e.g. agent.model claude-haiku-4-5)")
    set_p.add_argument("key", help="Dotted key (e.g. agent.model, provider.api_key)")
    set_p.add_argument("value", help="New value")

    backup_p = sub.add_parser("backup", help="Export or import local HushClaw state")
    backup_sub = backup_p.add_subparsers(dest="backup_command")
    backup_export_p = backup_sub.add_parser("export", help="Create a migration backup archive")
    backup_export_p.add_argument("output", nargs="?", help="Output .zip path (default: ./hushclaw-backup-YYYYmmdd-HHMMSS.zip)")
    backup_export_p.add_argument("--skip-config", action="store_true", help="Do not include hushclaw.toml in the archive")
    backup_export_p.add_argument("--skip-plugins", action="store_true", help="Do not include config-dir custom tools in the archive")
    backup_import_p = backup_sub.add_parser("import", help="Restore a migration backup archive")
    backup_import_p.add_argument("archive", help="Backup .zip created by 'hushclaw backup export'")
    backup_import_p.add_argument("--data-dir", help="Restore data into this directory instead of the archive/current default")
    backup_import_p.add_argument("--config-file", help="Restore config into this file instead of the default hushclaw.toml path")
    backup_import_p.add_argument("--plugin-dir", help="Restore custom tools into this directory instead of the default config-dir tools path")
    backup_import_p.add_argument("--skip-config", action="store_true", help="Do not restore hushclaw.toml from the archive")
    backup_import_p.add_argument("--skip-plugins", action="store_true", help="Do not restore config-dir custom tools from the archive")
    backup_import_p.add_argument("--force", action="store_true", help="Overwrite existing restore targets")
    backup_import_p.add_argument(
        "--database-key-stdin",
        action="store_true",
        help="Read an encrypted backup recovery key from standard input",
    )

    database_p = sub.add_parser("database", help="Inspect and protect the local database")
    database_sub = database_p.add_subparsers(dest="database_command")
    database_sub.add_parser("status", help="Show schema, integrity, encryption, and permission status")
    database_sub.add_parser("harden", help="Restrict database files and backups to the current OS user")
    database_sub.add_parser("encrypt", help="Atomically migrate the database and snapshots to SQLCipher")
    database_sub.add_parser("recovery-key", help="Print the recovery key for offline storage")

    reindex_p = sub.add_parser("reindex-memories", help="Rebuild vector index for all notes (run after changing embed_model)")
    reindex_p.add_argument("--batch-size", type=int, default=50, metavar="N",
                           help="Notes per batch (default: 50)")

    rebuild_beliefs_p = sub.add_parser("rebuild-beliefs", help="Rebuild Belief Map buckets from historical belief/interest notes")
    rebuild_beliefs_p.add_argument("--dry-run", action="store_true", help="Show planned buckets without modifying memory.db")

    serve_p = sub.add_parser("serve", help="Start the WebSocket server")
    serve_p.add_argument("--host", metavar="HOST")
    serve_p.add_argument("--port", type=int, metavar="PORT")
    serve_p.add_argument("--distro", metavar="DISTRO", default="personal",
                         help="Distribution profile to use: personal (default: personal)")

    agents_p = sub.add_parser("agents", help="Multi-agent management")
    agents_sub = agents_p.add_subparsers(dest="agents_command")
    agents_sub.add_parser("list", help="List configured agents")
    agents_run_p = agents_sub.add_parser("run", help="Run a message through a named agent")
    agents_run_p.add_argument("agent_name")
    agents_run_p.add_argument("message")
    agents_pipeline_p = agents_sub.add_parser(
        "pipeline", help="Run a message through a sequence of agents"
    )
    agents_pipeline_p.add_argument(
        "agent_names", help="Comma-separated agent names (e.g. 'researcher,writer')"
    )
    agents_pipeline_p.add_argument("message")

    return p


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    import os
    if args.provider:
        os.environ["HUSHCLAW_PROVIDER"] = args.provider
    if args.model:
        os.environ["HUSHCLAW_MODEL"] = args.model
    if args.log_level:
        os.environ["HUSHCLAW_LOG_LEVEL"] = args.log_level

    # ---- Commands that don't need agent initialization ----

    if args.command == "init":
        sys.exit(cmd_init(args))

    if args.command == "doctor":
        sys.exit(cmd_doctor(args))

    if args.command == "config":
        if args.config_command == "path":
            sys.exit(cmd_config_path(args))
        elif args.config_command == "set":
            sys.exit(cmd_config_set(args))
        elif args.config_command == "show":
            sys.exit(cmd_config_show(args))
        else:
            parser.parse_args(["config", "--help"])
        return

    if args.command == "backup":
        if args.backup_command == "export":
            sys.exit(cmd_backup_export(args))
        elif args.backup_command == "import":
            sys.exit(cmd_backup_import(args))
        else:
            parser.parse_args(["backup", "--help"])
        return

    if args.command == "database":
        try:
            if args.database_command == "status":
                sys.exit(cmd_database_status(args))
            elif args.database_command == "harden":
                sys.exit(cmd_database_harden(args))
            elif args.database_command == "encrypt":
                sys.exit(cmd_database_encrypt(args))
            elif args.database_command == "recovery-key":
                sys.exit(cmd_database_recovery_key(args))
            else:
                parser.parse_args(["database", "--help"])
        except Exception as exc:
            print(f"Database command failed: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # ---- All other commands need the runtime ----

    try:
        distro_id = getattr(args, "distro", None) or "personal"
        runtime = _make_runtime(distro_id)
    except Exception as e:
        _handle_agent_init_error(e)
        sys.exit(1)

    try:
        if args.command == "chat":
            sys.exit(cmd_chat(args, runtime))
        elif args.command == "remember":
            sys.exit(cmd_remember(args, runtime))
        elif args.command == "search":
            sys.exit(cmd_search(args, runtime))
        elif args.command == "sessions":
            if getattr(args, "sessions_command", None) == "resume":
                sys.exit(cmd_sessions_resume(args, runtime))
            else:
                sys.exit(cmd_sessions(args, runtime))
        elif args.command == "tools":
            if getattr(args, "tools_command", None) == "list":
                sys.exit(cmd_tools_list(args, runtime))
        elif args.command == "reindex-memories":
            sys.exit(cmd_reindex_memories(args, runtime))
        elif args.command == "rebuild-beliefs":
            sys.exit(cmd_rebuild_beliefs(args, runtime))
        elif args.command == "serve":
            sys.exit(cmd_serve(args, runtime))
        elif args.command == "agents":
            cmd = getattr(args, "agents_command", None)
            if cmd == "list":
                sys.exit(cmd_agents_list(args, runtime))
            elif cmd == "run":
                sys.exit(cmd_agents_run(args, runtime))
            elif cmd == "pipeline":
                sys.exit(cmd_agents_pipeline(args, runtime))
        else:
            # No subcommand → interactive REPL
            session_id = getattr(args, "session", None)
            _repl(runtime.agent, session_id=session_id)
    finally:
        runtime.close()
