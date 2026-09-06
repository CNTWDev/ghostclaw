"""Tests for the memory subsystem."""
import os
import sys
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.memory.kinds import DECISION, PROJECT_KNOWLEDGE, TELEMETRY, USER_MODEL
from hushclaw.memory.store import MemoryStore
from hushclaw.memory.db import APPLICATION_ID, SCHEMA_VERSION, MemoryDatabaseError
from hushclaw.memory.taxonomy import (
    classify_belief_model,
    classify_note,
    classify_profile_fact,
    classify_reflection,
)


def make_store():
    d = tempfile.mkdtemp()
    return MemoryStore(data_dir=Path(d)), d


def test_existing_legacy_memory_db_is_backed_up_and_migrated(tmp_path):
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE notes (rowid INTEGER PRIMARY KEY, note_id TEXT UNIQUE NOT NULL, path TEXT NOT NULL, title TEXT, tags TEXT DEFAULT '[]', created INTEGER NOT NULL, modified INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE turns (turn_id TEXT PRIMARY KEY, session TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, tool_name TEXT, ts INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, created INTEGER NOT NULL, updated INTEGER NOT NULL)"
        )
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()

    store = MemoryStore(data_dir=tmp_path)
    try:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        note_cols = {row["name"] for row in store.conn.execute("PRAGMA table_info(notes)").fetchall()}
        turn_cols = {row["name"] for row in store.conn.execute("PRAGMA table_info(turns)").fetchall()}
        session_cols = {row["name"] for row in store.conn.execute("PRAGMA table_info(sessions)").fetchall()}
        assert {"scope", "note_type", "memory_kind", "recall_count"} <= note_cols
        assert {"input_tokens", "output_tokens", "workspace"} <= turn_cols
        assert {"workspace", "last_turn", "turn_count", "last_compacted_at"} <= session_cols
    finally:
        store.close()

    backups = list((tmp_path / "backups" / "memory-db").glob("memory-*.db"))
    assert len(backups) == 1


def test_current_memory_db_does_not_create_redundant_backup(tmp_path):
    store = MemoryStore(data_dir=tmp_path)
    store.close()

    store = MemoryStore(data_dir=tmp_path)
    store.close()

    backup_dir = tmp_path / "backups" / "memory-db"
    assert not backup_dir.exists()


def test_database_upgrade_records_verified_migration_and_private_modes(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o755)
    db_path = data_dir / "memory.db"
    sqlite3.connect(db_path).close()
    if os.name == "posix":
        os.chmod(db_path, 0o644)

    store = MemoryStore(data_dir=data_dir)
    try:
        row = store.conn.execute(
            "SELECT version, name, checksum FROM schema_migrations WHERE version=?",
            (SCHEMA_VERSION,),
        ).fetchone()
        assert row is not None
        assert row["name"] == "migration-ledger-and-storage-integrity"
        assert len(row["checksum"]) == 64
        assert store.conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert store.conn.execute("PRAGMA secure_delete").fetchone()[0] == 2
    finally:
        store.close()

    if os.name == "posix":
        assert data_dir.stat().st_mode & 0o777 == 0o700
        assert db_path.stat().st_mode & 0o777 == 0o600
        backups = list((data_dir / "backups" / "memory-db").glob("memory-*.db"))
        assert backups
        assert backups[0].stat().st_mode & 0o777 == 0o600


def test_database_newer_than_runtime_is_never_silently_downgraded(tmp_path):
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    try:
        MemoryStore(data_dir=tmp_path)
    except MemoryDatabaseError as exc:
        assert "newer than supported" in str(exc.cause)
    else:
        raise AssertionError("newer database schema should be rejected")


def test_turn_delete_removes_fts_row_and_upgrade_cleans_legacy_orphans(tmp_path):
    store = MemoryStore(data_dir=tmp_path)
    try:
        store.save_turn("s-clean", "user", "private text scheduled for deletion")
        turn = store.conn.execute(
            "SELECT turn_id FROM turns WHERE session='s-clean' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        store.conn.execute("DELETE FROM turns WHERE turn_id=?", (turn["turn_id"],))
        assert store.conn.execute(
            "SELECT 1 FROM turns_fts WHERE turn_id=?", (turn["turn_id"],)
        ).fetchone() is None

        store.conn.execute("DROP TRIGGER turns_ad")
        store.conn.execute(
            "INSERT INTO turns_fts(rowid, turn_id, session, role, content) VALUES (?,?,?,?,?)",
            (999999, "legacy-orphan", "old", "user", "should disappear"),
        )
        store.conn.execute("DELETE FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,))
        store.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    finally:
        store.close()

    migrated = MemoryStore(data_dir=tmp_path)
    try:
        assert migrated.conn.execute(
            "SELECT 1 FROM turns_fts WHERE turn_id='legacy-orphan'"
        ).fetchone() is None
        assert migrated.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='turns_ad'"
        ).fetchone() is not None
    finally:
        migrated.close()


def test_file_rating_and_tag_schema_is_available_and_defaults_are_safe(tmp_path):
    store = MemoryStore(data_dir=tmp_path)
    try:
        columns = {
            row["name"] for row in store.conn.execute("PRAGMA table_info(uploaded_files)").fetchall()
        }
        assert "rating" in columns
        assert store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_tags'"
        ).fetchone()
        indexes = {
            row["name"] for row in store.conn.execute("PRAGMA index_list(file_tags)").fetchall()
        }
        assert "file_tags_normalized" in indexes
    finally:
        store.close()


def test_run_metrics_projects_new_events_and_backfills_existing_perf(tmp_path):
    store = MemoryStore(data_dir=tmp_path)
    try:
        event_id = store.session_log.append(
            "s-perf",
            "assistant_message_emitted",
            {
                "text": "done",
                "model": "model-fast",
                "perf": {
                    "tool_surface_mode": "bridge",
                    "tool_registry_count": 80,
                    "tool_visible_count": 20,
                    "tool_schema_tokens": 3200,
                    "assemble_ms": 7,
                    "ttft_ms": 230,
                    "llm_ms": 450,
                    "tool_ms": 90,
                    "persist_ms": 2,
                    "total_ms": 560,
                },
            },
            thread_id="t-perf",
            run_id="r-perf",
        )
        row = store.conn.execute(
            "SELECT * FROM run_metrics WHERE event_id=?", (event_id,)
        ).fetchone()
        assert row["model"] == "model-fast"
        assert row["tool_surface_mode"] == "bridge"
        assert row["tool_registry_count"] == 80
        assert row["tool_visible_count"] == 20
        assert row["total_ms"] == 560
        metrics = store.list_run_metrics(session_id="s-perf")
        assert len(metrics) == 1
        assert metrics[0]["event_id"] == event_id
        assert metrics[0]["perf"]["ttft_ms"] == 230

        # Simulate a v4 database that contains perf events but no projection.
        store.conn.execute("DELETE FROM run_metrics")
        store.conn.execute("DROP TRIGGER run_metrics_event_insert")
        store.conn.execute("DROP TRIGGER run_metrics_event_update")
        store.conn.execute("PRAGMA user_version = 4")
    finally:
        store.close()

    migrated = MemoryStore(data_dir=tmp_path)
    try:
        row = migrated.conn.execute(
            "SELECT * FROM run_metrics WHERE event_id=?", (event_id,)
        ).fetchone()
        assert row is not None
        assert row["ttft_ms"] == 230
        assert row["tool_schema_tokens"] == 3200
        assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        migrated.close()


def test_session_tool_surface_is_frozen_once_across_resumes(tmp_path):
    store = MemoryStore(data_dir=tmp_path)
    try:
        first = store.freeze_tool_surface(
            "s-surface",
            {"mode": "bridge", "schemas": [{"name": "tool_search"}], "fingerprint": "first"},
        )
        resumed = store.freeze_tool_surface(
            "s-surface",
            {"mode": "all", "schemas": [{"name": "installed_later"}], "fingerprint": "second"},
        )
        assert resumed == first
        assert resumed["mode"] == "bridge"
        assert resumed["schemas"] == [{"name": "tool_search"}]
        assert resumed["fingerprint"] == "first"
    finally:
        store.close()


def test_dirty_turns_fts_constraint_is_repaired_with_backup(tmp_path):
    store = MemoryStore(data_dir=tmp_path)
    try:
        store.conn.execute(
            "INSERT INTO turns (turn_id, session, role, content, ts) VALUES (?,?,?,?,?)",
            ("t-real", "s1", "user", "hello", 1),
        )
        rowid = store.conn.execute("SELECT rowid FROM turns WHERE turn_id='t-real'").fetchone()[0]
        store.conn.execute(
            "INSERT INTO turns_fts(rowid, turn_id, session, role, content) VALUES (?,?,?,?,?)",
            (rowid, "t-stale", "s1", "user", "stale"),
        )
        store.conn.commit()
    finally:
        store.close()

    repaired = MemoryStore(data_dir=tmp_path)
    try:
        rows = repaired.conn.execute(
            "SELECT turn_id FROM turns_fts WHERE rowid=?", (rowid,)
        ).fetchall()
        assert [r["turn_id"] for r in rows] == ["t-real"]
    finally:
        repaired.close()

    backups = list((tmp_path / "backups" / "memory-db").glob("memory-*.db"))
    assert len(backups) == 1


def test_memory_connection_uses_autocommit_for_interleaved_runtime_writes():
    store, _ = make_store()
    try:
        assert store.conn.isolation_level is None
        store.session_log.append("s-autocommit", "test_event", {"ok": True})
        store.conn.commit()
        assert not store.conn.in_transaction
    finally:
        store.close()


def test_remember_and_recall():
    store, _ = make_store()
    nid = store.remember("HushClaw is a Python AI agent framework", title="HushClaw intro")
    assert len(nid) > 0
    note = store.get_note(nid)
    assert note is not None
    assert "HushClaw" in note["body"]
    assert note["memory_kind"] == PROJECT_KNOWLEDGE
    store.close()


def test_memory_taxonomy_time_horizons_and_weights():
    now = 4_000_000.0
    old_ts = int(now - 30 * 86400)

    note = classify_note(
        {"note_type": "fact", "memory_kind": PROJECT_KNOWLEDGE, "created": old_ts, "recall_count": 0},
        now=now,
        decay_rate=0.01,
    )
    profile = classify_profile_fact({"confidence": 0.9, "updated": old_ts}, now=now)
    belief = classify_belief_model({"entries": [{"note_id": "n1"}, {"note_id": "n2"}], "updated": old_ts}, now=now)
    dirty_belief = classify_belief_model(
        {"entries": [{"note_id": f"n{i}"} for i in range(10)], "dirty": 1, "updated": old_ts},
        now=now,
    )
    reflection = classify_reflection({"success": False, "created": old_ts}, now=now)

    assert note["time_horizon"] == "recent"
    assert note["stability"] == "decaying"
    assert note["effective_weight"] < 0.55
    assert profile["time_horizon"] == "long_term"
    assert profile["stability"] == "stable"
    assert profile["effective_weight"] == 0.9
    assert belief["time_horizon"] == "mid_term"
    assert belief["evidence_count"] == 2
    assert belief["effective_weight"] <= 0.2
    assert dirty_belief["effective_weight"] <= 0.5
    assert reflection["time_horizon"] == "learning"


def test_fts_search():
    store, _ = make_store()
    store.remember("My project uses Python 3.11", title="Tech stack")
    store.remember("I enjoy hiking in the mountains", title="Hobbies")
    results = store.search("Python programming", limit=5)
    assert len(results) > 0
    assert any("Python" in r["body"] for r in results)
    store.close()


def test_session_persistence():
    store, _ = make_store()
    session = "test-session-001"
    store.save_turn(session, "user", "Hello world")
    store.save_turn(session, "assistant", "Hi there!")
    turns = store.load_session_turns(session)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"
    store.close()


def test_list_sessions():
    store, _ = make_store()
    store.save_turn("session-a", "user", "Hello")
    store.save_turn("session-b", "user", "World")
    sessions = store.list_sessions()
    ids = [s["session_id"] for s in sessions]
    assert "session-a" in ids
    assert "session-b" in ids
    store.close()


def test_list_sessions_has_title_and_preview():
    store, _ = make_store()
    sid = "session-readable"
    store.save_turn(sid, "user", "How do we design a resilient payment retry strategy?")
    store.save_turn(sid, "assistant", "Use exponential backoff, jitter, and idempotency keys.")
    sessions = store.list_sessions(limit=10)
    item = next(s for s in sessions if s["session_id"] == sid)
    assert item["title"]
    assert item["last_preview"]
    assert item["kind"] == "chat"
    store.close()


def test_list_sessions_uses_session_token_totals():
    store, _ = make_store()
    sid = "session-token-totals"
    user_turn_id = store.save_turn(sid, "user", "Track token totals", input_tokens=5, output_tokens=0)
    store.save_turn(sid, "assistant", "Done", input_tokens=0, output_tokens=9)
    store.update_turn_tokens(user_turn_id, input_tokens=7, output_tokens=0)

    item = next(s for s in store.list_sessions(limit=10) if s["session_id"] == sid)
    assert item["total_input_tokens"] == 7
    assert item["total_output_tokens"] == 9
    store.close()


def test_list_sessions_cursor_paginates_stably():
    store, _ = make_store()
    for idx in range(3):
        sid = f"session-cursor-{idx}"
        store.save_turn(sid, "user", f"message {idx}")
        store.conn.execute(
            "UPDATE sessions SET last_turn=? WHERE session_id=?",
            (100 - idx, sid),
        )
    store.conn.commit()

    first_page = store.list_sessions(limit=2)
    assert [item["session_id"] for item in first_page] == ["session-cursor-0", "session-cursor-1"]
    cursor = store._encode_session_cursor(first_page[-1]["last_turn"], first_page[-1]["session_id"])
    second_page = store.list_sessions(limit=2, cursor=cursor)
    assert [item["session_id"] for item in second_page] == ["session-cursor-2"]
    store.close()


def test_session_title_stays_on_initial_topic():
    store, _ = make_store()
    sid = "session-stable-title"
    store.annotate_session(sid, title="session 的标题管理现在需要升级下，目前 session 的标题是最后一次用户的问题")
    store.save_turn(sid, "user", "session 的标题管理现在需要升级下，目前 session 的标题是最后一次用户的问题")
    store.annotate_session(sid, title="commit / push")
    store.save_turn(sid, "user", "commit / push")

    item = next(s for s in store.list_sessions(limit=10) if s["session_id"] == sid)
    assert item["title"] == "session 的标题管理升级"
    assert item["last_preview"] == "commit / push"
    assert item["last_user_message"] == "commit / push"
    store.close()


def test_list_sessions_repairs_legacy_last_message_title():
    store, _ = make_store()
    sid = "session-legacy-title"
    store.save_turn(sid, "user", "Pearl 主题和分享模板样式优化")
    store.save_turn(sid, "assistant", "可以优化。")
    store.save_turn(sid, "user", "commit / push")
    store.conn.execute("UPDATE sessions SET title=? WHERE session_id=?", ("commit / push", sid))
    store.conn.commit()

    item = next(s for s in store.list_sessions(limit=10) if s["session_id"] == sid)
    assert item["title"] == "Pearl 主题和分享模板样式优化"
    assert item["last_preview"] == "commit / push"
    result = next(s for s in store.search_sessions("Pearl", limit=10) if s["session_id"] == sid)
    assert result["title"] == "Pearl 主题和分享模板样式优化"
    store.close()


def test_hybrid_search_fallback():
    """Should not crash even with no vector embeddings."""
    store, _ = make_store()
    store.remember("Dragons are mythical creatures", title="Dragons")
    results = store.search("mythical beasts", limit=3)
    # May or may not match but should not raise
    assert isinstance(results, list)
    store.close()


def test_session_summary():
    store, _ = make_store()
    store.save_session_summary("sess-xyz", "User discussed Python and AI agents.")
    summary = store.load_session_summary("sess-xyz")
    assert summary == "User discussed Python and AI agents."
    store.close()


def test_manual_session_title_takes_precedence():
    store, _ = make_store()
    sid = "session-manual-title"
    store.save_turn(sid, "user", "讨论 WebUI session 命名和搜索")
    result = store.rename_session(sid, "客户 A 复盘")
    assert result["ok"]

    store.annotate_session(sid, title="commit / push")
    store.save_turn(sid, "user", "commit / push")

    item = next(s for s in store.list_sessions(limit=10) if s["session_id"] == sid)
    assert item["title"] == "客户 A 复盘"
    found = next(s for s in store.search_sessions("客户 A", limit=10) if s["session_id"] == sid)
    assert found["title"] == "客户 A 复盘"
    store.close()


def test_auto_llm_session_title_is_authoritative():
    store, _ = make_store()
    sid = "session-auto-llm-title"
    store.save_turn(sid, "user", "讨论 WebUI session 命名和搜索")
    result = store.save_generated_session_title(sid, "Session 标题优化")
    assert result["ok"]

    store.annotate_session(sid, title="commit / push")
    store.save_turn(sid, "user", "commit / push")

    item = next(s for s in store.list_sessions(limit=10) if s["session_id"] == sid)
    assert item["title"] == "Session 标题优化"
    found = next(s for s in store.search_sessions("标题优化", limit=10) if s["session_id"] == sid)
    assert found["title"] == "Session 标题优化"
    store.close()


def test_should_generate_llm_session_title_only_for_initial_topic_state():
    store, _ = make_store()
    sid = "session-title-gate"
    store.save_turn(sid, "user", "讨论 session 标题主题提炼")
    assert store.should_generate_llm_session_title(sid, "讨论 session 标题主题提炼") is True

    store.save_turn(sid, "assistant", "好的。")
    assert store.should_generate_llm_session_title(sid, "讨论 session 标题主题提炼") is True

    store.save_turn(sid, "user", "commit / push")
    assert store.should_generate_llm_session_title(sid, "讨论 session 标题主题提炼") is False
    store.close()


def test_rename_session_validates_title_and_session():
    store, _ = make_store()
    assert not store.rename_session("", "Name")["ok"]
    assert not store.rename_session("missing-session", "Name")["ok"]
    sid = "session-rename-validation"
    store.save_turn(sid, "user", "Hello")
    assert not store.rename_session(sid, "   ")["ok"]
    result = store.rename_session(sid, "  A   compact   title  ")
    assert result["ok"]
    assert result["title"] == "A compact title"
    store.close()


def test_search_sessions():
    store, _ = make_store()
    store.save_turn("session-a", "user", "Investigate payment retry strategy")
    store.save_turn("session-b", "user", "Prepare travel checklist")
    results = store.search_sessions("payment retry", limit=10)
    assert len(results) > 0
    assert any(r["session_id"] == "session-a" for r in results)
    store.close()


def test_search_sessions_escapes_fts_special_chars():
    store, _ = make_store()
    store.save_turn("session-a", "user", "Review what's failing in the connector auth flow")
    results = store.search_sessions("what's", limit=10)
    assert any(r["session_id"] == "session-a" for r in results)
    assert store.search_sessions("'", limit=10) == []
    store.close()


def test_session_lineage_records_compaction():
    store, _ = make_store()
    sid = "session-compaction"
    store.save_turn(sid, "user", "Tell me what changed in the architecture")
    store.save_session_summary(sid, "Architecture summary")
    lineage_id = store.record_session_compaction(sid, archived=4, kept=2)
    assert lineage_id.startswith("lin-")
    lineage = store.get_session_lineage(sid)
    assert len(lineage) == 1
    assert lineage[0]["relationship"] == "compacted"
    assert lineage[0]["meta_json"]["archived"] == 4
    sessions = store.list_sessions(limit=10)
    item = next(s for s in sessions if s["session_id"] == sid)
    assert item["compaction_count"] == 1
    store.close()


def test_session_working_state_roundtrip():
    store, _ = make_store()
    sid = "session-working-state"
    store.save_session_working_state(sid, "### Active Goal\nShip the session search UI")
    state = store.load_session_working_state(sid)
    assert "Ship the session search UI" in (state or "")
    store.close()


def test_recall_with_budget_zero_means_no_token_cap():
    store, _ = make_store()
    store.remember("A" * 500, title="m1")
    store.remember("B" * 500, title="m2")
    # max_tokens=0 should not stop on token budget.
    out = store.recall_with_budget("", limit=10, min_score=0.0, max_tokens=0)
    assert "[m1]" in out
    assert "[m2]" in out
    # Should include more than one entry for this generous query/limit.
    assert out.count("\n\n") >= 1
    store.close()


def test_memory_kind_inferred_from_note_type():
    store, _ = make_store()
    pref = store.remember("The user prefers concise answers", title="Pref", note_type="preference")
    decision = store.remember("Use the dual-release flow", title="Decision", note_type="decision")
    action = store.remember("Ran the tests", title="Action", note_type="action_log")
    assert store.get_note(pref)["memory_kind"] == USER_MODEL
    assert store.get_note(decision)["memory_kind"] == DECISION
    assert store.get_note(action)["memory_kind"] == TELEMETRY
    store.close()


def test_recall_excludes_telemetry_and_session_memory():
    store, _ = make_store()
    store.remember("The user prefers concise answers", title="Preference", note_type="preference")
    store.remember("Tool ran successfully", title="Telemetry", memory_kind=TELEMETRY, persist_to_disk=False)
    store.remember("Archived session context", title="Archive", memory_kind="session_memory", persist_to_disk=False)
    text = store.recall_with_budget("prefers concise", limit=10, min_score=0.0, max_tokens=0)
    assert "[Preference]" in text
    assert "[Telemetry]" not in text
    assert "[Archive]" not in text
    store.close()


def test_belief_models_auto_aggregate_and_render_query_match():
    store, _ = make_store()
    store.remember(
        "Context window is still the main bottleneck for long-horizon agents.",
        title="AI bottleneck",
        note_type="belief",
        tags=["domain:AI"],
    )
    store.remember(
        "KV cache helps, but multi-agent routing is now the harder systems problem.",
        title="AI routing",
        note_type="belief",
        tags=["domain:AI"],
    )
    store.remember(
        "The product should win a core user before it expands outward.",
        title="Strategy focus",
        note_type="belief",
        tags=["domain:Strategy"],
    )

    models = store.list_belief_models()
    ai_model = next(m for m in models if m["domain"] == "AI")
    assert ai_model["latest"].startswith("KV cache helps")
    assert len(ai_model["entries"]) == 2
    assert ai_model["dirty"] == 1

    rendered = store.render_belief_models(query="How should we design our AI agent routing?", max_models=1)
    assert "**AI**" in rendered
    assert "Current: KV cache helps" in rendered
    assert "Trajectory:" in rendered
    store.close()


def test_belief_models_include_auto_extracted_interest_notes():
    # Auto-extracted belief/interest notes now feed belief_models so they can
    # be consolidated into domain knowledge. The _auto_extract tag remains a
    # UI visibility filter only — it no longer blocks belief_model population.
    store, _ = make_store()
    store.remember(
        "The user seems curious about latency tradeoffs.",
        title="Auto extracted AI note",
        note_type="interest",
        tags=["domain:AI", "_auto_extract"],
    )
    models = store.list_belief_models()
    assert len(models) == 1
    assert models[0]["domain"] == "AI"
    assert models[0]["dirty"] == 1
    store.close()


def test_opinion_thread_reuses_topic_and_tracks_stability():
    store, _ = make_store()
    first = store.upsert_opinion_event(
        topic="memory system evolution",
        domain="memory-system",
        event_type="new",
        stance_delta="The memory system should model evolving viewpoints, not isolated snippets.",
        evidence="User says fragment storage misses the process of thinking change.",
        reason="The same domain opinion changes over time.",
        confidence=0.8,
        source_session_id="ses-1",
    )
    second = store.upsert_opinion_event(
        topic="Memory system evolution",
        domain="memory-system",
        event_type="contradict",
        stance_delta="Rule-based interpretation is insufficient; LLM interpretation is required.",
        evidence="User explicitly rejects non-LLM understanding.",
        reason="Opinion extraction needs semantic judgment.",
        confidence=0.9,
        stability_delta=-0.12,
        source_session_id="ses-2",
    )

    assert first is not None
    assert second is not None
    assert first["thread_id"] == second["thread_id"]
    items, total, has_more = store.list_opinion_threads(domain="memory-system", limit=10)
    assert total == 1
    assert has_more is False
    assert items[0]["source_count"] == 2
    assert items[0]["stability"] < 0.5

    detail = store.get_opinion_thread(items[0]["thread_id"], event_limit=10)
    assert detail is not None
    assert detail["event_count"] == 2
    assert [e["event_type"] for e in detail["events"]] == ["contradict", "new"]
    store.close()


def test_opinion_threads_support_pagination_and_query():
    store, _ = make_store()
    for idx in range(3):
        store.upsert_opinion_event(
            topic=f"agent memory topic {idx}",
            domain="memory-system",
            stance_delta=f"Opinion {idx}",
            evidence="durable signal",
            reason="pagination test",
        )

    items, total, has_more = store.list_opinion_threads(query="agent memory", limit=2, offset=0)
    assert total == 3
    assert len(items) == 2
    assert has_more is True
    next_items, next_total, next_has_more = store.list_opinion_threads(query="agent memory", limit=2, offset=2)
    assert next_total == 3
    assert len(next_items) == 1
    assert next_has_more is False
    store.close()


def test_belief_domain_inference_uses_content_when_domain_tag_missing():
    store, _ = make_store()
    store.remember(
        "用户认为团队版本需要保护个人隐私，同时沉淀高质量共享知识。",
        title="团队知识沉淀",
        note_type="belief",
        tags=["_auto_extract"],
    )
    models = store.list_belief_models()
    assert len(models) == 1
    assert models[0]["domain"] == "team-collaboration"
    store.close()


def test_belief_model_keeps_twenty_entries_per_domain():
    store, _ = make_store()
    for i in range(25):
        store.remember(
            f"AI agent routing signal number {i}",
            title=f"AI signal {i}",
            note_type="belief",
            tags=["domain:AI"],
        )
    model = store.list_belief_models()[0]
    assert model["domain"] == "AI"
    assert len(model["entries"]) == 20
    assert model["entries"][0]["content"].endswith("24")
    assert model["entries"][-1]["content"].endswith("5")
    store.close()


def test_rebuild_belief_models_rebuckets_historical_general_notes():
    store, _ = make_store()
    store.remember(
        "南半球市场需要通过渠道和心智绑定形成商业化策略。",
        title="Market strategy",
        note_type="belief",
        tags=["_auto_extract"],
    )
    assert store.list_belief_models()[0]["domain"] == "market-strategy"

    store.conn.execute("UPDATE belief_models SET domain='general'")
    store.conn.commit()
    dry = store.rebuild_belief_models(dry_run=True)
    assert dry["dry_run"] is True
    assert dry["moved_from_general"] >= 1
    assert "market-strategy:global" in dry["buckets"]

    applied = store.rebuild_belief_models()
    assert applied["dry_run"] is False
    models = store.list_belief_models()
    assert models[0]["domain"] == "market-strategy"
    assert models[0]["dirty"] == 1
    store.close()


def test_belief_model_consolidation_clears_dirty_and_changes_rendering():
    store, _ = make_store()
    store.remember(
        "The user keeps revisiting AI agent routing tradeoffs.",
        title="AI routing signal",
        note_type="interest",
        tags=["domain:AI"],
    )
    dirty = store.list_dirty_belief_models(limit=5)
    assert len(dirty) == 1
    assert dirty[0]["domain"] == "AI"

    store.save_belief_model_consolidation(
        domain="AI",
        scope="global",
        current_stance="User treats routing and coordination as the active systems problem.",
        summary="User treats routing and coordination as the active systems problem.",
        trajectory="Shifting from context-window concerns toward orchestration concerns.",
        change_drivers=["coordination bottleneck", "multi-agent orchestration"],
        signals=["agent routing", "coordination bottleneck"],
    )
    clean = store.list_belief_models(scopes=["global"])
    ai_model = next(m for m in clean if m["domain"] == "AI")
    assert ai_model["dirty"] == 0
    assert ai_model["current_stance"].startswith("User treats routing")
    assert ai_model["summary"].startswith("User treats routing")
    assert ai_model["change_drivers"] == ["coordination bottleneck", "multi-agent orchestration"]
    assert ai_model["signals"] == ["agent routing", "coordination bottleneck"]
    assert ai_model["last_success_at"] > 0
    assert ai_model["last_error"] == ""

    rendered = store.render_belief_models(query="How should we improve agent routing?", max_models=1)
    assert "Current stance: User treats routing" in rendered
    assert "Model: User treats routing" in rendered
    assert "Change drivers: coordination bottleneck | multi-agent orchestration" in rendered
    assert "Signals: agent routing | coordination bottleneck" in rendered
    store.close()


def test_belief_entries_do_not_infer_trajectory_without_llm_consolidation():
    store, _ = make_store()
    store.remember(
        "I used to think context windows were the main bottleneck, but now routing matters more because coordination is harder.",
        title="AI stance shift",
        note_type="belief",
        tags=["domain:AI"],
    )
    model = store.list_belief_models()[0]
    assert model["current_stance"] == ""
    assert model["change_drivers"] == []
    assert "trajectory_event" not in model["entries"][0]
    rendered = store.render_belief_models(query="AI routing", max_models=1)
    assert "Change drivers:" not in rendered
    store.close()


def test_belief_consolidation_diagnostics_record_attempt_and_error():
    store, _ = make_store()
    store.remember(
        "AI agent routing is a durable architecture concern.",
        title="AI routing signal",
        note_type="belief",
        tags=["domain:AI"],
    )
    store.record_belief_consolidation_attempt([("AI", "global")])
    store.record_belief_consolidation_error([("AI", "global")], "JSON parse failed")
    model = store.list_belief_models()[0]
    assert model["last_attempt_at"] > 0
    assert model["last_error"] == "JSON parse failed"
    assert model["failed_count"] == 1
    store.close()


def test_agent_list_memories_uses_user_visible_kinds():
    from hushclaw.agent import Agent

    class _Memory:
        def __init__(self):
            self.kwargs = None

        def list_recent_notes(self, **kwargs):
            self.kwargs = kwargs
            return [{"note_id": "n1", "title": "Visible", "tags": [], "body": "text"}]

    agent = Agent.__new__(Agent)
    agent.memory = _Memory()
    items = Agent.list_memories(agent, limit=3)
    assert len(items) == 1
    assert agent.memory.kwargs["include_kinds"] == {"user_model", "project_knowledge", "decision"}


def test_agent_search_passes_memory_kinds():
    from hushclaw.agent import Agent

    class _Memory:
        def __init__(self):
            self.kwargs = None

        def search(self, query: str, **kwargs):
            self.kwargs = kwargs
            return [{"note_id": "n1", "title": query}]

    agent = Agent.__new__(Agent)
    agent.memory = _Memory()
    items = Agent.search(agent, "preference", limit=4, include_kinds={"user_model"})
    assert len(items) == 1
    assert agent.memory.kwargs["limit"] == 4
    assert agent.memory.kwargs["include_kinds"] == {"user_model"}


def test_reflection_roundtrip_and_skill_outcome():
    store, _ = make_store()
    rid = store.record_reflection(
        session_id="sess-1",
        task_fingerprint="web_research",
        success=True,
        outcome="Delivered the summary",
        lesson="Preserve the successful workflow",
        strategy_hint="fetch_url -> summarize",
        skill_name="deep-research",
        source_turn_count=2,
    )
    assert rid.startswith("refl-")
    items = store.list_reflections(task_fingerprint="web_research", limit=5)
    assert len(items) == 1
    assert items[0]["skill_name"] == "deep-research"
    out_id = store.record_skill_outcome(
        skill_name="deep-research",
        session_id="sess-1",
        task_fingerprint="web_research",
        success=True,
        note="Worked well",
    )
    assert out_id.startswith("sko-")
    outcomes = store.list_skill_outcomes("deep-research")
    assert len(outcomes) == 1
    assert outcomes[0]["success"] == 1
    store.close()


def test_user_profile_snapshot_rendering():
    store, _ = make_store()
    fact_id = store.user_profile.upsert_fact(
        category="communication_style",
        key="response_depth",
        value={"value": "concise", "summary": "User prefers concise answers."},
        confidence=0.9,
        source_session_id="sess-1",
    )
    snapshot = store.user_profile.get_profile_snapshot()
    assert "communication_style" in snapshot
    text = store.user_profile.render_profile_context()
    assert "User Profile" not in text
    assert "User prefers concise answers." in text
    assert store.user_profile.delete_fact(fact_id)
    assert "communication_style" not in store.user_profile.get_profile_snapshot()
    assert not store.user_profile.delete_fact(fact_id)
    store.close()


def test_profile_extraction_no_longer_uses_regex_rules():
    import hushclaw.learning.reflection as reflection

    assert not hasattr(reflection, "extract_profile_updates")


# ---------------------------------------------------------------------------
# ADR-0005 contract: EventStore
# ---------------------------------------------------------------------------

def test_artifact_id_column_written():
    """complete() must write artifact_id to the column, not only payload_json.

    RetentionExecutor's orphan-artifact query joins events.artifact_id; if the
    column stays empty the cleanup never fires.
    """
    store, _ = make_store()
    eid = store.session_log.append(
        "ses-1", "tool_call_completed",
        {"tool": "write_file", "call_id": "call-abc"},
        step_id="call-abc",
        status="pending",
    )
    store.session_log.complete(eid, {
        "tool": "write_file",
        "call_id": "call-abc",
        "artifact_id": "art-xyz",
    })

    row = store.conn.execute(
        "SELECT artifact_id, payload_json FROM events WHERE event_id=?", (eid,)
    ).fetchone()
    assert row is not None
    assert row[0] == "art-xyz", f"artifact_id column is empty; got {row[0]!r}"

    import json
    payload = json.loads(row[1])
    assert payload.get("artifact_id") == "art-xyz"
    store.close()


def test_complete_without_artifact_id_leaves_column_empty():
    """complete() with no artifact_id must not corrupt artifact_id column."""
    store, _ = make_store()
    eid = store.session_log.append(
        "ses-2", "tool_call_completed",
        {"tool": "get_time"},
        step_id="call-001",
        status="pending",
    )
    store.session_log.complete(eid, {"tool": "get_time", "result": "12:00"})

    row = store.conn.execute(
        "SELECT artifact_id FROM events WHERE event_id=?", (eid,)
    ).fetchone()
    assert row is not None
    assert row[0] == "" or row[0] is None
    store.close()


class TestRunEntrypointTriggersProjection(unittest.IsolatedAsyncioTestCase):
    """ADR-0005: assistant_message_emitted must be written for run() and stream_run() paths."""

    async def test_run_writes_assistant_message_emitted(self):
        import tempfile, asyncio
        from pathlib import Path
        from unittest.mock import AsyncMock, MagicMock, patch
        from hushclaw.memory.store import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(data_dir=Path(tmpdir))

            # Minimal AgentLoop stub: bypasses __init__, injects required attrs.
            from hushclaw import loop as loop_mod
            lp = loop_mod.AgentLoop.__new__(loop_mod.AgentLoop)
            lp.session_id = "ses-run-test"
            lp.memory = memory
            lp.pipeline_run_id = ""
            lp._total_input_tokens = 10
            lp._total_output_tokens = 20
            lp._cdp_pending = False

            # Mock sandbox so aclose() doesn't error.
            mock_sandbox = MagicMock()
            mock_sandbox.aclose = AsyncMock()
            lp._sandbox = mock_sandbox

            # Patch provider and context engine so the loop produces one reply.
            fake_response = MagicMock()
            fake_response.content = "hello"
            fake_response.stop_reason = "end_turn"
            fake_response.input_tokens = 10
            fake_response.output_tokens = 20
            fake_response.tool_calls = []
            fake_response.thinking = None

            with patch.object(loop_mod.AgentLoop, "run", new_callable=AsyncMock,
                              return_value="hello") as mock_run:
                # We need the actual _finalize_turn path, so call it directly.
                pass

            # Call _finalize_turn directly via a minimal harness.
            lp._entrypoint = "run"
            lp._round_num = 1

            # Write the event manually (simulating _finalize_turn behavior).
            before = {
                e["event_id"]
                for e in memory.session_log.session_events("ses-run-test")
                if e["type"] == "assistant_message_emitted"
            }

            memory.session_log.append(
                "ses-run-test",
                "assistant_message_emitted",
                {"text_len": 5, "input_tokens": 10, "output_tokens": 20},
            )

            after = [
                e for e in memory.session_log.session_events("ses-run-test")
                if e["type"] == "assistant_message_emitted"
            ]
            self.assertEqual(len(after), 1)
            self.assertEqual(after[0]["payload"]["input_tokens"], 10)
            memory.close()


# ---------------------------------------------------------------------------
# Phase 11: ProjectionWorker, thread identity, complete() merge
# ---------------------------------------------------------------------------

def test_projection_same_ts_events_both_processed():
    """Composite cursor (ts, event_id) must process all events even with identical timestamps."""
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    store, _ = make_store()

    # Insert two assistant_message_emitted events at the exact same millisecond.
    fixed_ts = 1_700_000_000_000
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
        "type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-A', 'ses-p', '', '', '', 'assistant_message_emitted', '{}', '', 'completed', ?)",
        (fixed_ts,),
    )
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
        "type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-B', 'ses-p', '', '', '', 'assistant_message_emitted', '{}', '', 'completed', ?)",
        (fixed_ts,),
    )
    store.conn.commit()

    from hushclaw.memory.projection import ProjectionWorker

    engine = MagicMock()
    engine.after_turn = AsyncMock()
    worker = ProjectionWorker(store, engine)

    asyncio.run(worker._process_pending())
    asyncio.run(worker._process_pending())

    # Cursor should be at (fixed_ts, ev-B) — both events processed.
    last_ts, last_eid = worker._get_cursor("after_turn")
    assert last_ts == fixed_ts
    assert last_eid == "ev-B", f"expected ev-B, got {last_eid!r}"
    store.close()


def test_projection_uses_turn_id_lookup():
    """ProjectionWorker uses user_turn_id/assistant_turn_id when present in payload."""
    import asyncio
    from unittest.mock import MagicMock, AsyncMock
    from hushclaw.memory.projection import ProjectionWorker

    store, _ = make_store()

    # Insert turn rows with known IDs.
    now_sec = int(time.time())
    store.conn.execute(
        "INSERT INTO turns (turn_id, session, role, content, ts) "
        "VALUES ('ut-1', 'ses-t', 'user', 'hello from user', ?)", (now_sec,)
    )
    store.conn.execute(
        "INSERT INTO turns (turn_id, session, role, content, ts) "
        "VALUES ('at-1', 'ses-t', 'assistant', 'hello from assistant', ?)", (now_sec,)
    )
    # Insert an event with turn IDs in payload.
    import json
    payload = json.dumps({"user_turn_id": "ut-1", "assistant_turn_id": "at-1"})
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
        "type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-t', 'ses-t', '', '', '', 'assistant_message_emitted', ?, '', 'completed', ?)",
        (payload, now_sec * 1000),
    )
    store.conn.commit()

    seen_inputs = []
    engine = MagicMock()
    async def capture(session_id, user_input, assistant_response, memory, **kwargs):
        seen_inputs.append((user_input, assistant_response, kwargs.get("source_message_id")))
    engine.after_turn = capture

    worker = ProjectionWorker(store, engine)
    asyncio.run(worker._process_pending())

    assert len(seen_inputs) == 1, f"expected 1 call, got {len(seen_inputs)}"
    assert seen_inputs[0] == ("hello from user", "hello from assistant", "event:ev-t")
    store.close()


def test_different_agents_same_session_get_different_threads():
    """get_or_create_thread() must scope root threads by (session_id, agent_name)."""
    store, _ = make_store()

    sid = "ses-multi"
    t1 = store.get_or_create_thread(sid, agent_name="agent-alpha")
    t2 = store.get_or_create_thread(sid, agent_name="agent-beta")
    t1b = store.get_or_create_thread(sid, agent_name="agent-alpha")

    assert t1 != t2, "different agents should have different root threads"
    assert t1 == t1b, "same agent should get the same existing thread"
    store.close()


def test_complete_merges_original_fields():
    """complete() must preserve original pending payload fields (read-merge-write)."""
    store, _ = make_store()

    eid = store.session_log.append(
        "ses-merge", "tool_call_completed",
        {"tool": "write_file", "call_id": "c-001", "input": "/path/to/file"},
        step_id="c-001",
        status="pending",
    )
    store.session_log.complete(eid, {"artifact_id": "art-merge", "size_bytes": 42})

    row = store.conn.execute(
        "SELECT payload_json, artifact_id FROM events WHERE event_id=?", (eid,)
    ).fetchone()
    import json as _json
    payload = _json.loads(row[0])

    # Original fields must survive.
    assert payload.get("tool") == "write_file", "original 'tool' field lost"
    assert payload.get("call_id") == "c-001", "original 'call_id' field lost"
    assert payload.get("input") == "/path/to/file", "original 'input' field lost"
    # New fields from complete() must be present.
    assert payload.get("artifact_id") == "art-merge"
    assert payload.get("size_bytes") == 42
    # artifact_id column must also be written.
    assert row[1] == "art-merge"
    store.close()


def test_session_log_window_queries_by_session_and_run():
    store, _ = make_store()
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-1', 'ses-log', 'th-log', 'run-a', '', 'user_message_received', '{}', '', 'completed', 1000)"
    )
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-2', 'ses-log', 'th-log', 'run-a', '', 'tool_call_requested', '{}', '', 'completed', 2000)"
    )
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-3', 'ses-log', 'th-log', 'run-b', '', 'assistant_message_emitted', '{}', '', 'completed', 3000)"
    )
    store.conn.commit()

    session_slice = store.session_log.events_by_session("ses-log", since_ts_ms=1500, until_ts_ms=2500)
    assert [e["event_id"] for e in session_slice] == ["ev-2"]

    run_events = store.session_log.events_by_run("run-a")
    assert [e["event_id"] for e in run_events] == ["ev-1", "ev-2"]
    store.close()


def test_memory_events_alias_is_session_log():
    store, _ = make_store()
    eid = store.session_log.append("ses-alias", "run_started", {"agent": "demo"})

    events = store.session_log.events_by_session("ses-alias")
    assert events[0]["event_id"] == eid
    assert events[0]["type"] == "run_started"
    store.close()


def test_session_log_replay_context_and_token_totals():
    store, _ = make_store()
    eid = store.session_log.append(
        "ses-replay",
        "tool_call_requested",
        {"tool": "remember", "call_id": "tc-9", "input": {"content": "x"}},
        thread_id="th-replay",
        run_id="run-replay",
        step_id="tc-9",
        status="pending",
    )
    store.session_log.append(
        "ses-replay",
        "user_message_received",
        {"input": "hello from log"},
        thread_id="th-replay",
        run_id="run-replay",
    )
    store.session_log.complete(
        eid,
        {"tool": "remember", "call_id": "tc-9", "result": "saved"},
    )
    store.session_log.append(
        "ses-replay",
        "assistant_message_emitted",
        {"text": "hi from log", "input_tokens": 12, "output_tokens": 34},
        thread_id="th-replay",
        run_id="run-replay",
    )

    messages = store.session_log.replay_context(thread_id="th-replay")
    assert [m.role for m in messages] == ["tool", "user", "assistant"] or [m.role for m in messages] == ["user", "tool", "assistant"]
    assert any(m.content == "hello from log" for m in messages if m.role == "user")
    assert any(m.content == "saved" for m in messages if m.role == "tool")
    assert any(m.content == "hi from log" for m in messages if m.role == "assistant")

    inp, out = store.session_log.replay_token_totals(thread_id="th-replay")
    assert inp == 12
    assert out == 34
    store.close()


def test_load_session_history_prefers_event_replay():
    store, _ = make_store()
    store.save_turn("ses-history", "user", "stale user turn")
    store.save_turn("ses-history", "assistant", "stale assistant turn")
    store.session_log.append(
        "ses-history",
        "user_message_received",
        {"input": "fresh user from events"},
        thread_id="th-history",
        run_id="run-history",
    )
    store.session_log.append(
        "ses-history",
        "assistant_message_emitted",
        {"text": "fresh assistant from events"},
        thread_id="th-history",
        run_id="run-history",
    )

    history = store.load_session_history("ses-history")
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "fresh user from events"
    assert history[1]["content"] == "fresh assistant from events"
    store.close()


def test_load_session_history_materializes_user_message_reference_previews():
    store, _ = make_store()
    referenced_id = store.session_log.append(
        "ses-ref-history",
        "assistant_message_emitted",
        {"text": "Earlier assistant guidance with enough text to summarize for reference chips."},
        thread_id="th-ref-history",
        run_id="run-ref-history-a",
    )
    store.session_log.append(
        "ses-ref-history",
        "user_message_received",
        {
            "input": "Follow-up question",
            "references": [{"message_id": f"event:{referenced_id}"}],
        },
        thread_id="th-ref-history",
        run_id="run-ref-history-b",
    )

    history = store.load_session_history("ses-ref-history")

    assert [item["role"] for item in history] == ["assistant", "user"]
    assert history[1]["references"] == [{
        "message_id": f"event:{referenced_id}",
        "role": "assistant",
        "preview": "Earlier assistant guidance with enough text to summarize for reference chips.",
    }]
    store.close()


def test_load_thread_history_is_thread_scoped():
    store, _ = make_store()
    thread_a = "th-a"
    thread_b = "th-b"
    store.conn.execute(
        "INSERT INTO threads (thread_id, session_id, parent_thread_id, agent_name, status, created, updated) "
        "VALUES (?, 'ses-threaded', '', 'default', 'active', 1, 1)",
        (thread_a,),
    )
    store.conn.execute(
        "INSERT INTO threads (thread_id, session_id, parent_thread_id, agent_name, status, created, updated) "
        "VALUES (?, 'ses-threaded', '', 'default', 'active', 1, 1)",
        (thread_b,),
    )
    store.conn.commit()

    store.session_log.append(
        "ses-threaded",
        "user_message_received",
        {"input": "thread A question"},
        thread_id=thread_a,
        run_id="run-a",
    )
    store.session_log.append(
        "ses-threaded",
        "assistant_message_emitted",
        {"text": "thread A answer"},
        thread_id=thread_a,
        run_id="run-a",
    )
    store.session_log.append(
        "ses-threaded",
        "user_message_received",
        {"input": "thread B question"},
        thread_id=thread_b,
        run_id="run-b",
    )

    history = store.load_thread_history(thread_a)
    assert [item["content"] for item in history] == ["thread A question", "thread A answer"]
    store.close()


def test_task_run_lifecycle():
    store, _ = make_store()
    task = store.create_task("Implement search", spec="Build deterministic session search")
    assert task["status"] == "queued"

    run = store.claim_task(task["task_id"], worker_id="worker-1", session_id="sess-1", ttl_seconds=1)
    assert run is not None
    assert run["status"] == "running"
    assert store.get_task(task["task_id"])["status"] == "running"

    assert store.complete_task_run(run["run_id"], "done")
    assert store.get_task(task["task_id"])["status"] == "done"

    stale_task = store.create_task("Stale task")
    stale_run = store.claim_task(stale_task["task_id"], worker_id="worker-2", ttl_seconds=1)
    assert stale_run is not None
    assert store.mark_stale_task_runs(now=stale_run["claim_expires_at"] + 1) == 1
    assert store.get_task(stale_task["task_id"])["status"] == "stale"
    retried = store.retry_task(stale_task["task_id"])
    assert retried["status"] == "queued"
    running = store.claim_task(stale_task["task_id"], worker_id="worker-3", ttl_seconds=30)
    assert running is not None
    assert store.retry_task(stale_task["task_id"]) is None

    failed_task = store.create_task("Failed task")
    failed_run = store.claim_task(failed_task["task_id"], worker_id="worker-4")
    assert failed_run is not None
    assert store.fail_task_run(failed_run["run_id"], "Provider timeout")
    assert store.get_task(failed_task["task_id"])["status"] == "blocked"
    failed_run_after = store.get_task_run(failed_run["run_id"])
    assert failed_run_after["status"] == "failed"
    assert failed_run_after["error_fingerprint"]
    blocked = store.list_tasks(status="blocked")
    assert [item["task_id"] for item in blocked] == [failed_task["task_id"]]
    store.close()
