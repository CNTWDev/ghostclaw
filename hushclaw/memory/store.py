"""MemoryStore: unified facade over Markdown, FTS5, and vector search."""
from __future__ import annotations

import asyncio
import json
import math
import random
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from hushclaw.memory.artifacts import ArtifactStore
from hushclaw.memory.db import DB_NAME, MemoryDatabaseError, backup_existing_db, open_db, rebuild_fts_trigram
from hushclaw.memory.encryption import (
    DATABASE_ERRORS,
    INTEGRITY_ERRORS,
    OPERATIONAL_ERRORS,
    DatabaseKeyStore,
    database_file_format,
)
from hushclaw.memory.events import EventStore, _conn_lock
from hushclaw.memory.markdown import MarkdownStore
from hushclaw.memory.session_log import SessionLog
from hushclaw.memory.tasks import (
    TASK_CLAIMABLE_STATUSES,
    TASK_RUN_STATUS_FAILED,
    TASK_RUN_STATUS_RUNNING,
    TASK_RUN_STATUS_STALE,
    TASK_RUN_STATUS_SUCCEEDED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_DONE,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_STALE,
    TaskRunStore,
)
from hushclaw.memory.user_profile import UserProfileStore
from hushclaw.memory.fts import FTSSearch, _build_fts_query
from hushclaw.memory.kinds import (
    RECALL_MEMORY_KINDS,
    SYSTEM_MEMORY_TAGS,
    USER_VISIBLE_MEMORY_KINDS,
    infer_memory_kind,
)
from hushclaw.memory.vectors import VectorStore
from hushclaw.util.ids import make_id

# FTS score threshold above which vector search is skipped (saves embed cost)
_FTS_SHORTCUT_THRESHOLD = 0.8

# Recall cache TTL in seconds (same query within same session)
_CACHE_TTL = 30.0

INSIGHT_REVIEWED_TAG = "_insight_reviewed"
INSIGHT_TAG = "insight"

_SESSION_TITLE_MAX = 80
_SESSION_TITLE_SOURCE_MANUAL = "manual"
_SESSION_TITLE_SOURCE_AUTO_LOCAL = "auto_local"
_SESSION_TITLE_SOURCE_AUTO_LLM = "auto_llm"
_SESSION_TITLE_LOCKED_SOURCES = frozenset({
    _SESSION_TITLE_SOURCE_MANUAL,
    _SESSION_TITLE_SOURCE_AUTO_LLM,
})


class MemoryStore:
    """Single entry point for all memory operations."""

    def __init__(
        self,
        data_dir: Path,
        embed_provider: str = "local",
        embed_model: str = "",
        api_key: str = "",
        fts_weight: float = 0.6,
        vec_weight: float = 0.4,
        database_encryption: str = "auto",
    ) -> None:
        self.data_dir = data_dir
        self.notes_dir = data_dir / "notes"
        self.sessions_dir = data_dir / "sessions"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self.fts_weight = fts_weight
        self.vec_weight = vec_weight

        self.conn: sqlite3.Connection | None = None
        backup_path: Path | None = None
        try:
            self.database_encryption = database_encryption
            self.conn = open_db(data_dir, database_encryption=database_encryption)
            self._event_store = EventStore(self.conn)
            self.session_log = SessionLog(self.conn, self._event_store)
            self.artifacts = ArtifactStore(self.conn, data_dir)
            self._task_runs = TaskRunStore(self.conn)
            self._md = MarkdownStore(self.notes_dir, self.conn)
            self._fts = FTSSearch(self.conn)
            self._vec = VectorStore(self.conn, embed_provider, api_key, embed_model)
            self.user_profile = UserProfileStore(self.conn)

            # Session recall cache: (session_id, query) → (result_str, timestamp)
            self._recall_cache: dict[tuple[str, str], tuple[str, float]] = {}
            self._backfill_sessions()
            self._backfill_turns_fts()
        except MemoryDatabaseError:
            raise
        except INTEGRITY_ERRORS as exc:
            if self.conn is not None:
                db_path = data_dir / DB_NAME
                encrypted = database_file_format(db_path) == "sqlcipher-or-unknown"
                key = DatabaseKeyStore(data_dir).get()[0] if encrypted else ""
                backup_path = backup_existing_db(
                    data_dir, db_path, encrypted=encrypted, key=key
                )
                try:
                    rebuild_fts_trigram(self.conn)
                    self._backfill_sessions()
                    self._backfill_turns_fts()
                    return
                except Exception as retry_exc:
                    exc = retry_exc
                    self.conn.close()
            raise MemoryDatabaseError(
                f"could not initialize memory store: {exc}",
                data_dir=data_dir,
                db_path=data_dir / DB_NAME,
                backup_path=backup_path,
                cause=exc,
            ) from exc
        except DATABASE_ERRORS as exc:
            if self.conn is not None:
                self.conn.close()
            raise MemoryDatabaseError(
                f"could not initialize memory store: {exc}",
                data_dir=data_dir,
                db_path=data_dir / DB_NAME,
                backup_path=backup_path,
                cause=exc,
            ) from exc

    # ------------------------------------------------------------------
    # Notes API
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        scope: str = "global",
        persist_to_disk: bool = True,
        note_type: str = "fact",
        memory_kind: str = "",
        source_message_id: str = "",
    ) -> str:
        """Persist a note and index it. Returns note_id.

        persist_to_disk=False stores the note in SQLite only (no .md file).
        """
        tags = tags or []
        resolved_kind = infer_memory_kind(
            note_type=note_type,
            tags=tags,
            memory_kind=memory_kind,
        )
        note_id = self._md.write_note(
            content, title=title, tags=tags, scope=scope,
            persist_to_disk=persist_to_disk, note_type=note_type, memory_kind=resolved_kind,
        )
        source_mid = str(source_message_id or "").strip()
        if source_mid:
            self.conn.execute(
                "UPDATE notes SET source_message_id=? WHERE note_id=?",
                (source_mid, note_id),
            )
            self.conn.commit()
        self._vec.index(note_id, f"{title}\n{content}")
        # Auto-aggregate belief/interest notes into belief_models.
        # _auto_extract tag is a UI visibility filter only — it does NOT block
        # belief/interest signals from feeding the domain knowledge model.
        if note_type in {"belief", "interest"}:
            domain = self.infer_belief_domain(content, tags=tags, title=title)
            self._append_to_belief_model(domain, scope, note_id, content, note_type)
        return note_id

    def get_note(self, note_id: str) -> dict | None:
        return self._md.read_note(note_id)

    def update_note(self, note_id: str, content: str, tags: list[str] | None = None) -> bool:
        ok = self._md.update_note(note_id, content, tags)
        if ok:
            self._vec.index(note_id, content)
        return ok

    def delete_note(self, note_id: str) -> bool:
        return self._md.delete_note(note_id)

    @staticmethod
    def _parse_note_tags(raw) -> list[str]:
        if isinstance(raw, list):
            return [str(t) for t in raw if str(t).strip()]
        try:
            parsed = json.loads(raw or "[]")
        except Exception:
            parsed = []
        if not isinstance(parsed, list):
            return []
        return [str(t) for t in parsed if str(t).strip()]

    def update_note_tags(self, note_id: str, tags: list[str]) -> bool:
        row = self.conn.execute(
            "SELECT note_id, path FROM notes WHERE note_id=?",
            (note_id,),
        ).fetchone()
        if row is None:
            return False
        clean_tags: list[str] = []
        for tag in tags:
            value = str(tag or "").strip()
            if value and value not in clean_tags:
                clean_tags.append(value)
        now = int(time.time())
        self.conn.execute(
            "UPDATE notes SET tags=?, modified=? WHERE note_id=?",
            (json.dumps(clean_tags, ensure_ascii=False), now, note_id),
        )
        rowid = self.conn.execute("SELECT rowid FROM notes WHERE note_id=?", (note_id,)).fetchone()
        if rowid is not None:
            self.conn.execute(
                "UPDATE notes_fts SET tags=? WHERE rowid=?",
                (json.dumps(clean_tags, ensure_ascii=False), rowid["rowid"]),
            )
        self.conn.commit()
        return True

    def list_notes_by_tag(
        self,
        tag: str,
        *,
        limit: int = 50,
        offset: int = 0,
        note_types: set[str] | None = None,
        memory_kinds: set[str] | None = None,
    ) -> tuple[list[dict], bool]:
        needle = str(tag or "").strip()
        if not needle:
            return [], False
        fetch_limit = max(1, int(limit)) + 1
        params: list[object] = [f'%"{needle}"%']
        clauses = ["n.tags LIKE ?"]
        if note_types:
            placeholders = ",".join("?" * len(note_types))
            clauses.append(f"n.note_type IN ({placeholders})")
            params.extend(sorted(note_types))
        if memory_kinds:
            placeholders = ",".join("?" * len(memory_kinds))
            clauses.append(f"n.memory_kind IN ({placeholders})")
            params.extend(sorted(memory_kinds))
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.tags, n.scope, n.note_type, n.memory_kind, n.created, n.modified, b.body "
            "FROM notes n JOIN note_bodies b USING(note_id) "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY n.created DESC LIMIT ? OFFSET ?",
            (*params, fetch_limit, max(0, int(offset))),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                tags = json.loads(item.get("tags") or "[]")
            except Exception:
                tags = []
            if needle not in tags:
                continue
            item["tags"] = tags
            result.append(item)
        has_more = len(result) > int(limit)
        if has_more:
            result = result[: int(limit)]
        return result, has_more

    @staticmethod
    def classify_insight_quality(body: str, title: str = "") -> tuple[str, list[str], str]:
        text = " ".join((body or title or "").split()).strip()
        stripped = text.strip(" \t\r\n。，、,.;；:：\"'（）()[]【】「」『』-*_")
        lower = stripped.lower()
        flags: list[str] = []
        if not stripped:
            flags.append("empty")
        if len(stripped) < 20:
            flags.append("too_short")
        if stripped.startswith((
            "并", "以及", "并且", "另外", "然后", "且", "并将", "还有", "包括",
            "and ", "then ", "also ",
        )):
            flags.append("fragment_prefix")
        if stripped.endswith((",", "，", ";", "；", ":", "：", '"', "'", "？", "?")):
            flags.append("fragment_suffix")
        substantive = re.findall(r"[\w\u4e00-\u9fff]", stripped)
        if len(substantive) < 8:
            flags.append("low_substance")
        elif len(substantive) / max(len(stripped), 1) < 0.45:
            flags.append("low_signal_ratio")
        if any(p in stripped or p in lower for p in (
            "保存到记忆", "并保存到记忆", "已保存到记忆", "save to memory", "saved to memory",
        )):
            flags.append("memory_instruction")
        if re.search(r"(哪里|如何|怎么|为什么|能否|是否|什么|which|what|how|why|whether).{0,12}$", stripped, re.I):
            flags.append("question_fragment")
        if re.search(r"^(?:助手|系统|本轮|这次|建议|输出|分析|用户问|user asks|assistant)", stripped, re.I):
            flags.append("turn_artifact")
        if re.search(r"^(?:the |a |an )?(performance|collaborate|transition|growth|strategy)\\b.{0,35}$", lower):
            flags.append("english_fragment")
        if flags:
            hard_flags = {"empty", "too_short", "fragment_prefix", "fragment_suffix", "low_substance", "memory_instruction", "question_fragment", "english_fragment"}
            severity = "delete" if hard_flags & set(flags) else "review"
        else:
            severity = "ok"
        reason_map = {
            "empty": "empty content",
            "too_short": "too short to be durable",
            "fragment_prefix": "looks like a sentence fragment",
            "fragment_suffix": "ends like an unfinished question or clause",
            "low_substance": "too little substantive content",
            "low_signal_ratio": "low signal-to-noise ratio",
            "memory_instruction": "memory instruction artifact",
            "question_fragment": "question fragment",
            "turn_artifact": "turn artifact, not a durable insight",
            "english_fragment": "short English fragment",
        }
        reason = ", ".join(reason_map.get(flag, flag) for flag in flags) or "high-signal candidate"
        return severity, flags, reason

    def list_insight_notes(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        view: str = "curated",
    ) -> tuple[list[dict], bool]:
        fetch_limit = max(1, int(limit)) + 1
        view = str(view or "curated").strip().lower()
        if view not in {"curated", "suggested", "all"}:
            view = "curated"
        clauses: list[str] = []
        params: list[object] = []
        if view == "curated":
            clauses.append("n.tags LIKE ?")
            params.append(f'%"{INSIGHT_TAG}"%')
        elif view == "suggested":
            clauses.append("n.tags LIKE ?")
            clauses.append("n.tags NOT LIKE ?")
            clauses.append("n.tags NOT LIKE ?")
            clauses.append("n.note_type IN ('belief', 'interest')")
            clauses.append("n.memory_kind = ?")
            params.extend([f'%"_auto_extract"%', f'%"{INSIGHT_TAG}"%', f'%"{INSIGHT_REVIEWED_TAG}"%', "user_model"])
        else:
            clauses.append("(n.tags LIKE ? OR (n.note_type IN ('belief', 'interest') AND n.memory_kind = ?))")
            params.extend([f'%"{INSIGHT_TAG}"%', "user_model"])
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.tags, n.scope, n.note_type, n.memory_kind, n.created, n.modified, b.body "
            "FROM notes n JOIN note_bodies b USING(note_id) "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY n.created DESC LIMIT ? OFFSET ?",
            (*params, fetch_limit, max(0, int(offset))),
        ).fetchall()
        result: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            item = dict(row)
            note_id = str(item.get("note_id") or "")
            if not note_id or note_id in seen:
                continue
            tags = self._parse_note_tags(item.get("tags"))
            note_type = str(item.get("note_type") or "")
            memory_kind = str(item.get("memory_kind") or "")
            is_curated = INSIGHT_TAG in tags
            is_memory_insight = note_type in {"belief", "interest"} and memory_kind == "user_model"
            is_suggested = "_auto_extract" in tags and is_memory_insight and INSIGHT_REVIEWED_TAG not in tags
            if view == "curated" and not is_curated:
                continue
            if view == "suggested" and (is_curated or not is_suggested):
                continue
            if view == "all" and not is_curated and not is_memory_insight:
                continue
            item["tags"] = tags
            item["source_type"] = "curated" if is_curated else "memory"
            if is_suggested:
                severity, flags, reason = self.classify_insight_quality(item.get("body", ""), item.get("title", ""))
                item["quality"] = severity
                item["quality_flags"] = flags
                item["quality_reason"] = reason
            result.append(item)
            seen.add(note_id)
        has_more = len(result) > int(limit)
        if has_more:
            result = result[: int(limit)]
        return result, has_more

    def preview_insight_cleanup(self, *, limit: int = 50) -> dict:
        items, has_more = self.list_insight_notes(limit=max(1, int(limit)), offset=0, view="suggested")
        auto_delete: list[dict] = []
        review: list[dict] = []
        for item in items:
            quality = str(item.get("quality") or "review")
            if quality == "delete":
                auto_delete.append(item)
            else:
                review.append(item)
        return {
            "auto_delete_candidates": auto_delete,
            "review_candidates": review,
            "has_more": has_more,
            "limit": max(1, int(limit)),
        }

    def apply_insight_cleanup(
        self,
        *,
        auto_delete_ids: list[str] | None = None,
        delete_ids: list[str] | None = None,
        keep_ids: list[str] | None = None,
        promote_ids: list[str] | None = None,
    ) -> dict:
        def _ids(values: list[str] | None) -> list[str]:
            seen_local: set[str] = set()
            out: list[str] = []
            for value in values or []:
                note_id = str(value or "").strip()
                if note_id and note_id not in seen_local:
                    out.append(note_id)
                    seen_local.add(note_id)
            return out

        deleted = 0
        for note_id in _ids([*(_ids(auto_delete_ids)), *(_ids(delete_ids))]):
            if self.delete_note(note_id):
                deleted += 1

        kept = 0
        for note_id in _ids(keep_ids):
            note = self.get_note(note_id)
            if not note:
                continue
            tags = self._parse_note_tags(note.get("tags"))
            if INSIGHT_REVIEWED_TAG not in tags:
                tags.append(INSIGHT_REVIEWED_TAG)
            if self.update_note_tags(note_id, tags):
                kept += 1

        promoted = 0
        for note_id in _ids(promote_ids):
            note = self.get_note(note_id)
            if not note:
                continue
            tags = self._parse_note_tags(note.get("tags"))
            if INSIGHT_TAG not in tags:
                tags.insert(0, INSIGHT_TAG)
            if INSIGHT_REVIEWED_TAG not in tags:
                tags.append(INSIGHT_REVIEWED_TAG)
            if self.update_note_tags(note_id, tags):
                promoted += 1

        return {"deleted": deleted, "kept": kept, "promoted": promoted}

    def delete_notes_by_source_message(self, source_message_id: str) -> int:
        mid = str(source_message_id or "").strip()
        if not mid:
            return 0
        rows = self.conn.execute(
            "SELECT note_id, scope FROM notes WHERE source_message_id=?",
            (mid,),
        ).fetchall()
        scopes = sorted({str(row["scope"] or "global") for row in rows})
        count = 0
        for row in rows:
            if self._md.delete_note(row["note_id"]):
                count += 1
        if count:
            self.rebuild_belief_models(scopes=scopes or None)
        return count

    def note_exists_with_title(self, title: str) -> bool:
        """Return True if any note with this exact title already exists."""
        row = self.conn.execute(
            "SELECT 1 FROM notes WHERE title=? LIMIT 1", (title,)
        ).fetchone()
        return row is not None

    def delete_by_scope(self, scope: str) -> int:
        """Delete all notes with the given scope. Returns the number deleted."""
        rows = self.conn.execute(
            "SELECT note_id FROM notes WHERE scope=?", (scope,)
        ).fetchall()
        count = 0
        for row in rows:
            if self._md.delete_note(row["note_id"]):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Belief models API
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_domain_from_tags(tags: list[str]) -> str:
        """Return the domain from a 'domain:xxx' tag, or 'general'."""
        for t in tags:
            if t.startswith("domain:"):
                domain = t[7:].strip()
                if domain:
                    return domain
        return "general"

    _DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("AI", ("ai", "agent", "llm", "模型", "智能体", "token", "意图识别", "多模态")),
        ("architecture", ("架构", "kernel", "runtime", "toolregistry", "toolexecutor", "agentloop", "分层", "接口")),
        ("connectors", ("connector", "oauth", "github", "google workspace", "notion", "jira", "连接器")),
        ("memory-system", ("memory", "belief", "profile", "recall", "context", "记忆", "沉淀", "知识库", "上下文")),
        ("team-collaboration", ("团队", "共享", "权限", "隐私", "协作", "team", "workspace", "组织", "审计")),
        ("market-strategy", ("市场", "南半球", "新兴市场", "渠道", "商业化", "mindset", "voice share", "market share", "心智")),
        ("product-strategy", ("产品", "用户", "体验", "交互", "webui", "工作流", "入口", "定位")),
        ("life-planning", ("香港", "购房", "房产", "教育", "身份", "家庭", "财务")),
    )

    @classmethod
    def infer_belief_domain(cls, content: str, tags: list[str] | None = None, title: str = "") -> str:
        """Infer a stable belief domain from explicit tags first, then content keywords."""
        tags = tags or []
        explicit = cls._extract_domain_from_tags(tags)
        if explicit != "general":
            return explicit

        tag_text = " ".join(str(t).strip() for t in tags if str(t).strip() and not str(t).startswith("_"))
        haystack = f"{title}\n{tag_text}\n{content}".lower()
        best_domain = "general"
        best_score = 0
        for domain, keywords in cls._DOMAIN_RULES:
            score = sum(1 for kw in keywords if cls._belief_keyword_matches(haystack, kw))
            if score > best_score:
                best_domain = domain
                best_score = score
        return best_domain

    @staticmethod
    def _belief_keyword_matches(haystack: str, keyword: str) -> bool:
        kw = keyword.lower().strip()
        if not kw:
            return False
        if re.fullmatch(r"[a-z0-9_.-]{1,3}", kw):
            return re.search(rf"(?<![a-z0-9_.-]){re.escape(kw)}(?![a-z0-9_.-])", haystack) is not None
        return kw in haystack

    def _append_to_belief_model(
        self, domain: str, scope: str, note_id: str, content: str, note_type: str
    ) -> None:
        """Upsert belief_model for (domain, scope): update latest + prepend entry."""
        now = int(time.time())
        row = self.conn.execute(
            "SELECT entries FROM belief_models WHERE domain=? AND scope=?",
            (domain, scope),
        ).fetchone()
        entries: list[dict] = json.loads(row["entries"]) if row else []
        entries.insert(0, {
            "ts": now,
            "note_id": note_id,
            "content": content,
            "note_type": note_type,
        })
        entries = entries[:20]
        self.conn.execute(
            """INSERT INTO belief_models (
                   domain, scope, latest, entries, current_stance, summary, trajectory,
                   change_drivers, signals,
                   last_consolidated, last_attempt_at, last_success_at, last_error, failed_count,
                   dirty, updated
               )
               VALUES (?, ?, ?, ?, '', '', '', '[]', '[]', 0, 0, 0, '', 0, 1, ?)
               ON CONFLICT(domain, scope) DO UPDATE SET
                 latest=excluded.latest, entries=excluded.entries, dirty=1, updated=excluded.updated""",
            (domain, scope, content, json.dumps(entries, ensure_ascii=False), now),
        )
        self.conn.commit()

    def list_belief_models(self, scopes: list[str] | None = None) -> list[dict]:
        """Return all belief_models matching scopes, newest-updated first."""
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            rows = self.conn.execute(
                f"""SELECT domain, scope, latest, entries, current_stance, summary, trajectory,
                           change_drivers, signals,
                           last_consolidated, last_attempt_at, last_success_at, last_error,
                           failed_count, dirty, updated
                    FROM belief_models
                    WHERE scope IN ({placeholders}) ORDER BY updated DESC""",
                scopes,
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT domain, scope, latest, entries, current_stance, summary, trajectory,
                          change_drivers, signals,
                          last_consolidated, last_attempt_at, last_success_at, last_error,
                          failed_count, dirty, updated
                   FROM belief_models ORDER BY updated DESC"""
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "domain": r["domain"],
                "scope": r["scope"],
                "latest": r["latest"],
                "entries": json.loads(r["entries"]),
                "current_stance": r["current_stance"] or "",
                "summary": r["summary"] or "",
                "trajectory": r["trajectory"] or "",
                "change_drivers": json.loads(r["change_drivers"] or "[]"),
                "signals": json.loads(r["signals"] or "[]"),
                "last_consolidated": int(r["last_consolidated"] or 0),
                "last_attempt_at": int(r["last_attempt_at"] or 0),
                "last_success_at": int(r["last_success_at"] or 0),
                "last_error": r["last_error"] or "",
                "failed_count": int(r["failed_count"] or 0),
                "dirty": int(r["dirty"] or 0),
                "updated": r["updated"],
            })
        return result

    def list_dirty_belief_models(
        self,
        *,
        scopes: list[str] | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """Return dirty belief models, newest first, for async consolidation."""
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            rows = self.conn.execute(
                f"""SELECT domain, scope, latest, entries, current_stance, summary, trajectory,
                           change_drivers, signals,
                           last_consolidated, last_attempt_at, last_success_at, last_error,
                           failed_count, dirty, updated
                    FROM belief_models
                    WHERE dirty=1 AND scope IN ({placeholders})
                    ORDER BY updated DESC
                    LIMIT ?""",
                (*scopes, max(1, int(limit))),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT domain, scope, latest, entries, current_stance, summary, trajectory,
                          change_drivers, signals,
                          last_consolidated, last_attempt_at, last_success_at, last_error,
                          failed_count, dirty, updated
                   FROM belief_models
                   WHERE dirty=1
                   ORDER BY updated DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "domain": r["domain"],
                "scope": r["scope"],
                "latest": r["latest"] or "",
                "entries": json.loads(r["entries"] or "[]"),
                "current_stance": r["current_stance"] or "",
                "summary": r["summary"] or "",
                "trajectory": r["trajectory"] or "",
                "change_drivers": json.loads(r["change_drivers"] or "[]"),
                "signals": json.loads(r["signals"] or "[]"),
                "last_consolidated": int(r["last_consolidated"] or 0),
                "last_attempt_at": int(r["last_attempt_at"] or 0),
                "last_success_at": int(r["last_success_at"] or 0),
                "last_error": r["last_error"] or "",
                "failed_count": int(r["failed_count"] or 0),
                "dirty": int(r["dirty"] or 0),
                "updated": int(r["updated"] or 0),
            })
        return out

    def save_belief_model_consolidation(
        self,
        *,
        domain: str,
        scope: str,
        current_stance: str = "",
        summary: str = "",
        trajectory: str = "",
        change_drivers: list[str] | None = None,
        signals: list[str] | None = None,
    ) -> None:
        """Persist async model-powered consolidation results and clear dirty flag."""
        now = int(time.time())
        clean_signals = [str(s).strip()[:120] for s in (signals or []) if str(s).strip()]
        clean_drivers = [str(s).strip()[:160] for s in (change_drivers or []) if str(s).strip()]
        self.conn.execute(
            """UPDATE belief_models
               SET current_stance=?, summary=?, trajectory=?, change_drivers=?, signals=?,
                   last_consolidated=?,
                   last_success_at=?, last_error='', failed_count=0, dirty=0
               WHERE domain=? AND scope=?""",
            (
                current_stance.strip()[:220],
                summary.strip()[:220],
                trajectory.strip()[:220],
                json.dumps(clean_drivers[:3], ensure_ascii=False),
                json.dumps(clean_signals[:3], ensure_ascii=False),
                now,
                now,
                domain,
                scope,
            ),
        )
        self.conn.commit()

    def record_belief_consolidation_attempt(self, domains: list[tuple[str, str]]) -> None:
        """Record that consolidation was attempted for these (domain, scope) pairs."""
        now = int(time.time())
        for domain, scope in domains:
            self.conn.execute(
                "UPDATE belief_models SET last_attempt_at=? WHERE domain=? AND scope=?",
                (now, domain, scope),
            )
        self.conn.commit()

    def record_belief_consolidation_error(self, domains: list[tuple[str, str]], error: str) -> None:
        """Persist the latest consolidation error for observability."""
        now = int(time.time())
        message = str(error or "")[:300]
        for domain, scope in domains:
            self.conn.execute(
                """UPDATE belief_models
                   SET last_attempt_at=?, last_error=?, failed_count=failed_count + 1
                   WHERE domain=? AND scope=?""",
                (now, message, domain, scope),
            )
        self.conn.commit()

    def rebuild_belief_models(self, *, dry_run: bool = False, scopes: list[str] | None = None) -> dict:
        """Rebuild belief_models from historical belief/interest notes using domain inference."""
        where = ["n.note_type IN ('belief', 'interest')"]
        params: list[object] = []
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            where.append(f"n.scope IN ({placeholders})")
            params.extend(scopes)
        rows = self.conn.execute(
            f"""SELECT n.note_id, n.title, n.tags, n.scope, n.note_type, n.created, b.body
                FROM notes n
                JOIN note_bodies b ON b.note_id = n.note_id
                WHERE {' AND '.join(where)}
                ORDER BY n.created ASC""",
            params,
        ).fetchall()

        buckets: dict[tuple[str, str], list[dict]] = {}
        moved_from_general = 0
        for r in rows:
            tags = json.loads(r["tags"] or "[]")
            body = r["body"] or ""
            title = r["title"] or ""
            old_domain = self._extract_domain_from_tags(tags)
            domain = self.infer_belief_domain(body, tags=tags, title=title)
            if old_domain == "general" and domain != "general":
                moved_from_general += 1
            key = (domain, r["scope"] or "global")
            buckets.setdefault(key, []).append({
                "ts": int(r["created"] or 0),
                "note_id": r["note_id"],
                "content": body,
                "note_type": r["note_type"],
            })

        bucket_counts = {
            f"{domain}:{scope}": len(entries)
            for (domain, scope), entries in sorted(buckets.items())
        }
        if dry_run:
            return {
                "dry_run": True,
                "notes_scanned": len(rows),
                "bucket_count": len(buckets),
                "moved_from_general": moved_from_general,
                "buckets": bucket_counts,
            }

        if scopes:
            placeholders = ",".join("?" * len(scopes))
            self.conn.execute(f"DELETE FROM belief_models WHERE scope IN ({placeholders})", scopes)
        else:
            self.conn.execute("DELETE FROM belief_models")
        now = int(time.time())
        for (domain, scope), entries in buckets.items():
            newest_first = sorted(entries, key=lambda e: int(e.get("ts") or 0), reverse=True)[:20]
            latest = str(newest_first[0].get("content") or "") if newest_first else ""
            updated = int(newest_first[0].get("ts") or now) if newest_first else now
            self.conn.execute(
                """INSERT INTO belief_models (
                       domain, scope, latest, entries, current_stance, summary, trajectory,
                       change_drivers, signals,
                       last_consolidated, last_attempt_at, last_success_at, last_error, failed_count,
                       dirty, updated
                   )
                   VALUES (?, ?, ?, ?, '', '', '', '[]', '[]', 0, 0, 0, '', 0, 1, ?)""",
                (domain, scope, latest, json.dumps(newest_first, ensure_ascii=False), updated),
            )
        self.conn.commit()
        return {
            "dry_run": False,
            "notes_scanned": len(rows),
            "bucket_count": len(buckets),
            "moved_from_general": moved_from_general,
            "buckets": bucket_counts,
        }

    @staticmethod
    def _belief_query_terms(query: str) -> set[str]:
        """Extract lightweight lowercase query terms for domain matching."""
        if not query:
            return set()
        terms = {
            t.lower()
            for t in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)
            if len(t.strip()) >= 2
        }
        return terms

    @classmethod
    def _score_belief_model(cls, model: dict, query: str) -> float:
        """Return a precision-focused routing score for one belief model.

        Routing signal priority (high → low):
          1. domain name parts (split compound names like AI/LLM)
          2. signals — LLM-extracted key phrases
          3. summary — LLM-synthesised stance

        Raw entry text and latest content are intentionally excluded to avoid
        false positives from incidental keyword overlap.
        """
        terms = cls._belief_query_terms(query)
        # No query → pure timestamp so caller can rank by recency.
        if not terms:
            return float(model.get("updated") or 0)

        score = 0.0

        # 1. Domain name — split compound domains (e.g. "AI/LLM" → {ai, llm})
        raw_domain = str(model.get("domain") or "").lower()
        domain_parts = set(re.split(r"[/\-_\s]+", raw_domain)) - {""}
        for part in domain_parts:
            if part in terms:
                score += 6.0           # exact part match
            elif any(part in t or t in part for t in terms):
                score += 3.0           # substring match
                break

        # 2. Signals — LLM-refined key phrases (highest-precision route signal)
        raw_signals = model.get("signals") or []
        if isinstance(raw_signals, str):
            try:
                raw_signals = json.loads(raw_signals)
            except Exception:
                raw_signals = []
        signal_text = " ".join(str(s) for s in raw_signals).lower()
        signal_terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", signal_text))
        score += len(terms & signal_terms) * 3.0

        # 3. LLM stance fields — secondary route signals.
        stance_text = " ".join([
            str(model.get("current_stance") or ""),
            str(model.get("summary") or ""),
            str(model.get("trajectory") or ""),
            " ".join(str(d) for d in (model.get("change_drivers") or [])),
        ]).lower()
        if stance_text:
            stance_terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", stance_text))
            score += len(terms & stance_terms) * 1.5

        # Tiny timestamp tiebreaker — never changes domain ranking, only breaks ties.
        score += min(float(model.get("updated") or 0) / 1_000_000_000_000, 0.5)
        return score

    @staticmethod
    def _summarize_belief_evolution(entries: list[dict]) -> tuple[str, str]:
        """Return (history_line, trajectory_line) for prompt injection."""
        if len(entries) <= 1:
            note_type = str(entries[0].get("note_type") or "") if entries else ""
            if note_type == "interest":
                return "", "→ Signal: active interest observed, but no prior evolution yet"
            return "", ""

        previous = [str(e.get("content") or "").strip() for e in entries[1:4] if str(e.get("content") or "").strip()]
        note_types = [str(e.get("note_type") or "") for e in entries[:4]]
        unique_previous = []
        for item in previous:
            if item not in unique_previous:
                unique_previous.append(item)

        history_line = ""
        if unique_previous:
            history_line = "→ Before: " + " | ".join(item[:80] for item in unique_previous[:2])

        belief_count = sum(1 for t in note_types if t == "belief")
        interest_count = sum(1 for t in note_types if t == "interest")
        if belief_count and interest_count:
            trajectory = "→ Trajectory: mixes stable viewpoints with repeated curiosity in this domain"
        elif interest_count >= 2:
            trajectory = "→ Trajectory: recurring exploration signal across multiple turns"
        elif belief_count >= 2:
            trajectory = "→ Trajectory: repeated judgment pattern, suggesting an emerging stable stance"
        else:
            trajectory = "→ Trajectory: early signal only; model is still sparse"
        return history_line, trajectory

    # Minimum routing score required to inject a belief model.
    # With an active query, domains scoring below this threshold are skipped entirely.
    _BELIEF_ROUTE_MIN_SCORE: float = 3.0

    def render_belief_models(
        self,
        scopes: list[str] | None = None,
        *,
        query: str = "",
        max_chars: int = 700,
        max_models: int = 3,
    ) -> str:
        """Format belief models for prompt injection. Query-aware, compact, and scoped."""
        models = self.list_belief_models(scopes=scopes)
        if not models:
            return ""

        has_query = bool(query.strip())
        # Without a query, show only the single most recently updated domain
        # to avoid flooding context with unrelated beliefs.
        effective_max = max_models if has_query else 1

        ranked = sorted(
            models,
            key=lambda m: self._score_belief_model(m, query),
            reverse=True,
        )

        lines: list[str] = []
        char_budget = max_chars
        selected = 0
        for m in ranked:
            if selected >= effective_max:
                break
            # Apply min-score gate when there is an active query.
            # ranked is descending, so once we fall below threshold all remaining will too.
            if has_query and self._score_belief_model(m, query) < self._BELIEF_ROUTE_MIN_SCORE:
                break
            entries = m["entries"]
            count = len(entries)
            if count == 0:
                continue
            from datetime import datetime, timezone
            date_str = datetime.fromtimestamp(m["updated"], tz=timezone.utc).strftime("%Y-%m-%d")
            header = f"**{m['domain']}** ({count} belief{'s' if count != 1 else ''}, updated {date_str})"
            latest = m["latest"][:120]
            line = f"{header}\n→ Current: {latest}"
            current_stance = str(m.get("current_stance") or "").strip()
            summary = str(m.get("summary") or "").strip()
            trajectory = str(m.get("trajectory") or "").strip()
            change_drivers = [str(s).strip() for s in (m.get("change_drivers") or []) if str(s).strip()]
            signals = [str(s).strip() for s in (m.get("signals") or []) if str(s).strip()]
            history_line, fallback_trajectory = self._summarize_belief_evolution(entries)
            if current_stance and current_stance != latest:
                line += f"\n→ Current stance: {current_stance[:160]}"
            if summary:
                line += f"\n→ Model: {summary[:160]}"
            if history_line:
                line += f"\n{history_line}"
            if trajectory:
                line += f"\n→ Trajectory: {trajectory[:160]}"
            elif fallback_trajectory:
                line += f"\n{fallback_trajectory}"
            if change_drivers:
                line += "\n→ Change drivers: " + " | ".join(s[:80] for s in change_drivers[:2])
            if signals:
                line += "\n→ Signals: " + " | ".join(s[:60] for s in signals[:2])
            if char_budget - len(line) < 0:
                break
            lines.append(line)
            char_budget -= len(line)
            selected += 1
        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    # Opinion timeline API
    # ------------------------------------------------------------------

    _OPINION_EVENT_TYPES = {"new", "reinforce", "refine", "contradict", "reverse", "generalize"}

    @staticmethod
    def _normalize_opinion_topic(topic: str) -> str:
        text = str(topic or "").strip().lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^\w\u4e00-\u9fff\s-]+", "", text)
        return text[:120]

    @staticmethod
    def _bounded_float(value: object, default: float = 0.5, *, min_value: float = 0.0, max_value: float = 1.0) -> float:
        try:
            num = float(value)
        except Exception:
            num = default
        if math.isnan(num) or math.isinf(num):
            num = default
        return min(max_value, max(min_value, num))

    @classmethod
    def _opinion_row_payload(cls, row: sqlite3.Row) -> dict:
        return {
            "thread_id": row["thread_id"],
            "topic": row["topic"] or "",
            "topic_key": row["topic_key"] or "",
            "domain": row["domain"] or "general",
            "scope": row["scope"] or "global",
            "current_stance": row["current_stance"] or "",
            "summary": row["summary"] or "",
            "confidence": float(row["confidence"] or 0.0),
            "stability": float(row["stability"] or 0.0),
            "source_count": int(row["source_count"] or 0),
            "created": int(row["created"] or 0),
            "updated": int(row["updated"] or 0),
        }

    @staticmethod
    def _opinion_event_payload(row: sqlite3.Row) -> dict:
        return {
            "event_id": row["event_id"],
            "thread_id": row["thread_id"],
            "event_type": row["event_type"] or "",
            "stance_delta": row["stance_delta"] or "",
            "evidence": row["evidence"] or "",
            "reason": row["reason"] or "",
            "confidence": float(row["confidence"] or 0.0),
            "stability_delta": float(row["stability_delta"] or 0.0),
            "source_session_id": row["source_session_id"] or "",
            "source_message_id": row["source_message_id"] or "",
            "created": int(row["created"] or 0),
        }

    def _find_opinion_thread(self, *, topic_key: str, domain: str, scope: str) -> dict | None:
        if not topic_key:
            return None
        row = self.conn.execute(
            """SELECT thread_id, topic, topic_key, domain, scope, current_stance, summary,
                      confidence, stability, source_count, created, updated
               FROM opinion_threads
               WHERE domain=? AND scope=? AND topic_key=?
               LIMIT 1""",
            (domain, scope, topic_key),
        ).fetchone()
        if row:
            return self._opinion_row_payload(row)

        candidates = self.conn.execute(
            """SELECT thread_id, topic, topic_key, domain, scope, current_stance, summary,
                      confidence, stability, source_count, created, updated
               FROM opinion_threads
               WHERE domain=? AND scope=?
               ORDER BY updated DESC
               LIMIT 25""",
            (domain, scope),
        ).fetchall()
        topic_terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", topic_key))
        if not topic_terms:
            return None
        best_row = None
        best_score = 0.0
        for row in candidates:
            candidate_key = str(row["topic_key"] or "")
            topic_numbers = set(re.findall(r"\d+", topic_key))
            candidate_numbers = set(re.findall(r"\d+", candidate_key))
            if topic_numbers != candidate_numbers:
                continue
            candidate_terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", candidate_key))
            if not candidate_terms:
                continue
            overlap = len(topic_terms & candidate_terms)
            containment = topic_key in candidate_key or candidate_key in topic_key
            score = overlap / max(len(topic_terms | candidate_terms), 1)
            if containment:
                score += 0.35
            if score > best_score:
                best_score = score
                best_row = row
        if best_row is not None and best_score >= 0.62:
            return self._opinion_row_payload(best_row)
        return None

    def upsert_opinion_event(
        self,
        *,
        topic: str,
        domain: str = "general",
        scope: str = "global",
        event_type: str = "new",
        stance_delta: str = "",
        evidence: str = "",
        reason: str = "",
        confidence: float = 0.5,
        stability_delta: float = 0.0,
        source_session_id: str = "",
        source_message_id: str = "",
    ) -> dict | None:
        topic_s = str(topic or "").strip()[:160]
        if not topic_s:
            return None
        domain_s = str(domain or "general").strip()[:80] or "general"
        scope_s = str(scope or "global").strip()[:120] or "global"
        event_type_s = str(event_type or "new").strip().lower()
        if event_type_s not in self._OPINION_EVENT_TYPES:
            event_type_s = "new"
        stance_s = str(stance_delta or "").strip()[:500]
        evidence_s = str(evidence or "").strip()[:500]
        reason_s = str(reason or "").strip()[:500]
        if not (stance_s or evidence_s or reason_s):
            return None

        conf = self._bounded_float(confidence, 0.5)
        supplied_stability_delta = self._bounded_float(
            stability_delta,
            0.0,
            min_value=-1.0,
            max_value=1.0,
        )
        topic_key = self._normalize_opinion_topic(topic_s)
        now = int(time.time())
        thread = self._find_opinion_thread(topic_key=topic_key, domain=domain_s, scope=scope_s)
        thread_id = str(thread.get("thread_id")) if thread else "opt-" + make_id()

        if thread is None:
            self.conn.execute(
                """INSERT INTO opinion_threads (
                       thread_id, topic, topic_key, domain, scope, current_stance, summary,
                       confidence, stability, source_count, created, updated
                   )
                   VALUES (?, ?, ?, ?, ?, '', '', ?, 0.5, 0, ?, ?)""",
                (thread_id, topic_s, topic_key, domain_s, scope_s, conf, now, now),
            )
            previous_conf = conf
            previous_stability = 0.5
            previous_source_count = 0
        else:
            previous_conf = float(thread.get("confidence") or 0.5)
            previous_stability = float(thread.get("stability") or 0.5)
            previous_source_count = int(thread.get("source_count") or 0)

        event_id = "ope-" + make_id()
        self.conn.execute(
            """INSERT INTO opinion_events (
                   event_id, thread_id, event_type, stance_delta, evidence, reason,
                   confidence, stability_delta, source_session_id, source_message_id, created
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                thread_id,
                event_type_s,
                stance_s,
                evidence_s,
                reason_s,
                conf,
                supplied_stability_delta,
                str(source_session_id or "").strip(),
                str(source_message_id or "").strip(),
                now,
            ),
        )

        default_stability_delta = {
            "new": 0.0,
            "reinforce": 0.06,
            "refine": 0.03,
            "generalize": 0.04,
            "contradict": -0.08,
            "reverse": -0.18,
        }.get(event_type_s, 0.0)
        effective_delta = supplied_stability_delta if supplied_stability_delta else default_stability_delta
        next_stability = self._bounded_float(previous_stability + effective_delta, 0.5)
        next_confidence = self._bounded_float((previous_conf * previous_source_count + conf) / max(previous_source_count + 1, 1), conf)
        prev_stance = thread.get("current_stance", "") if thread else ""
        prev_summary = thread.get("summary", "") if thread else ""
        next_stance = stance_s or prev_stance
        next_summary = evidence_s or reason_s or prev_summary
        if next_summary.strip() == next_stance.strip():
            next_summary = ""
        self.conn.execute(
            """UPDATE opinion_threads
               SET current_stance=?, summary=?, confidence=?, stability=?,
                   source_count=source_count + 1, updated=?
               WHERE thread_id=?""",
            (next_stance[:500], next_summary[:500], next_confidence, next_stability, now, thread_id),
        )
        self.conn.commit()
        return self.get_opinion_thread(thread_id, event_limit=1)

    def list_opinion_threads(
        self,
        *,
        domain: str = "",
        scope: str = "",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int, bool]:
        limit_i = max(1, min(int(limit), 200))
        offset_i = max(0, int(offset))
        clauses: list[str] = []
        params: list[object] = []
        domain_s = str(domain or "").strip()
        scope_s = str(scope or "").strip()
        query_s = str(query or "").strip()
        if domain_s:
            clauses.append("domain=?")
            params.append(domain_s)
        if scope_s:
            clauses.append("scope=?")
            params.append(scope_s)
        if query_s:
            like = f"%{self._normalize_opinion_topic(query_s)}%"
            clauses.append("(topic_key LIKE ? OR current_stance LIKE ? OR summary LIKE ?)")
            params.extend([like, f"%{query_s}%", f"%{query_s}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = int(self.conn.execute(f"SELECT COUNT(*) AS c FROM opinion_threads {where}", params).fetchone()["c"])
        rows = self.conn.execute(
            f"""SELECT thread_id, topic, topic_key, domain, scope, current_stance, summary,
                      confidence, stability, source_count, created, updated
               FROM opinion_threads
               {where}
               ORDER BY updated DESC
               LIMIT ? OFFSET ?""",
            (*params, limit_i, offset_i),
        ).fetchall()
        items = [self._opinion_row_payload(row) for row in rows]
        return items, total, offset_i + len(items) < total

    def get_opinion_thread(
        self,
        thread_id: str,
        *,
        event_limit: int = 50,
        event_offset: int = 0,
    ) -> dict | None:
        tid = str(thread_id or "").strip()
        if not tid:
            return None
        row = self.conn.execute(
            """SELECT thread_id, topic, topic_key, domain, scope, current_stance, summary,
                      confidence, stability, source_count, created, updated
               FROM opinion_threads
               WHERE thread_id=?""",
            (tid,),
        ).fetchone()
        if not row:
            return None
        limit_i = max(1, min(int(event_limit), 200))
        offset_i = max(0, int(event_offset))
        total = int(self.conn.execute(
            "SELECT COUNT(*) AS c FROM opinion_events WHERE thread_id=?",
            (tid,),
        ).fetchone()["c"])
        events = self.conn.execute(
            """SELECT event_id, thread_id, event_type, stance_delta, evidence, reason,
                      confidence, stability_delta, source_session_id, source_message_id, created
               FROM opinion_events
               WHERE thread_id=?
               ORDER BY created DESC, rowid DESC
               LIMIT ? OFFSET ?""",
            (tid, limit_i, offset_i),
        ).fetchall()
        out = self._opinion_row_payload(row)
        out["events"] = [self._opinion_event_payload(e) for e in events]
        out["event_count"] = total
        out["event_offset"] = offset_i
        out["event_limit"] = limit_i
        out["events_has_more"] = offset_i + len(events) < total
        return out

    def search_by_tag(self, tag: str, limit: int = 10) -> list[dict]:
        """Return notes that carry the given tag (exact match in JSON array)."""
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.recall_count, n.memory_kind, b.body FROM notes n "
            "LEFT JOIN note_bodies b USING(note_id), json_each(n.tags) "
            "WHERE json_each.value = ? LIMIT ?",
            (tag, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def increment_recall_count(self, note_id: str) -> None:
        """Increment a note's recall_count for lightweight retrieval telemetry."""
        self.conn.execute(
            "UPDATE notes SET recall_count = recall_count + 1 WHERE note_id=?",
            (note_id,),
        )
        self.conn.commit()

    def get_promotion_candidates(self, threshold: int = 5, tag: str = "_skill") -> list[dict]:
        """Return skills with recall_count >= threshold, ordered by recall_count desc."""
        rows = self.conn.execute(
            "SELECT n.note_id, n.title, n.recall_count, b.body "
            "FROM notes n LEFT JOIN note_bodies b USING(note_id), json_each(n.tags) "
            "WHERE json_each.value = ? AND n.recall_count >= ? "
            "ORDER BY n.recall_count DESC",
            (tag, threshold),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_notes(
        self,
        limit: int = 100,
        offset: int = 0,
        exclude_tags: list[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """Return the most recently modified notes with their bodies."""
        clauses: list[str] = []
        params: list[object] = []
        if exclude_tags:
            ph = ",".join("?" * len(exclude_tags))
            clauses.append(
                f"NOT EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value IN ({ph}))"
            )
            params.extend(exclude_tags)
        if include_kinds:
            ph = ",".join("?" * len(include_kinds))
            clauses.append(f"n.memory_kind IN ({ph})")
            params.extend(sorted(include_kinds))
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.conn.execute(
            f"SELECT n.note_id, n.title, n.tags, n.note_type, n.memory_kind, b.body FROM notes n "
            f"LEFT JOIN note_bodies b USING(note_id) "
            f"{where_sql}"
            f"ORDER BY n.modified DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [
            {**dict(r), "tags": json.loads(r["tags"] or "[]")}
            for r in rows
        ]

    def list_recent_notes_by_scopes(
        self,
        scopes: list[str],
        limit: int = 100,
        offset: int = 0,
        exclude_tags: list[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """Return the most recently modified notes whose scope is in `scopes`."""
        scope_ph = ",".join("?" * len(scopes))
        clauses = [f"n.scope IN ({scope_ph})"]
        params: list[object] = list(scopes)
        if exclude_tags:
            tag_ph = ",".join("?" * len(exclude_tags))
            clauses.append(
                f"NOT EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value IN ({tag_ph}))"
            )
            params.extend(exclude_tags)
        if include_kinds:
            kind_ph = ",".join("?" * len(include_kinds))
            clauses.append(f"n.memory_kind IN ({kind_ph})")
            params.extend(sorted(include_kinds))
        rows = self.conn.execute(
            f"SELECT n.note_id, n.title, n.tags, n.scope, n.note_type, n.memory_kind, b.body FROM notes n "
            f"LEFT JOIN note_bodies b USING(note_id) "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY n.modified DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [
            {**dict(r), "tags": json.loads(r["tags"] or "[]")}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------------

    def _fetch_note_metadata(self, note_ids: list[str]) -> dict[str, tuple[int, str, str]]:
        if not note_ids:
            return {}
        placeholders = ",".join("?" * len(note_ids))
        rows = self.conn.execute(
            f"SELECT note_id, recall_count, note_type, memory_kind FROM notes WHERE note_id IN ({placeholders})",
            note_ids,
        ).fetchall()
        return {
            row["note_id"]: (
                int(row["recall_count"] or 0),
                row["note_type"] or "fact",
                row["memory_kind"] or "project_knowledge",
            )
            for row in rows
        }

    def search(
        self,
        query: str,
        limit: int = 5,
        include_kinds: set[str] | None = None,
        exclude_tags: list[str] | None = None,
    ) -> list[dict]:
        """Hybrid FTS + vector search, merged by score."""
        visible_kinds = include_kinds if include_kinds is not None else USER_VISIBLE_MEMORY_KINDS
        blocked_tags = list(dict.fromkeys((exclude_tags or []) + sorted(SYSTEM_MEMORY_TAGS)))
        fts_results = {r["note_id"]: r for r in self._fts.search(query, limit * 2, exclude_tags=blocked_tags)}
        vec_results = {r["note_id"]: r for r in self._vec.search(query, limit * 2, exclude_tags=blocked_tags)}

        all_ids = set(fts_results) | set(vec_results)
        merged = []
        for nid in all_ids:
            fts_score = fts_results.get(nid, {}).get("score_fts", 0.0)
            vec_score = vec_results.get(nid, {}).get("score_vec", 0.0)
            combined = self.fts_weight * fts_score + self.vec_weight * vec_score
            note = fts_results.get(nid) or vec_results.get(nid, {})
            merged.append({
                "note_id": nid,
                "title": note.get("title", ""),
                "body": note.get("body", ""),
                "tags": note.get("tags", []),
                "score": combined,
            })

        meta = self._fetch_note_metadata([r["note_id"] for r in merged])
        filtered = []
        for r in merged:
            _rc, note_type, memory_kind = meta.get(r["note_id"], (0, "fact", "project_knowledge"))
            if visible_kinds and memory_kind not in visible_kinds:
                continue
            r["note_type"] = note_type
            r["memory_kind"] = memory_kind
            filtered.append(r)

        filtered.sort(key=lambda x: x["score"], reverse=True)
        return filtered[:limit]

    def recall(self, query: str, limit: int = 5) -> str:
        """Return a formatted string of top search results for LLM injection."""
        results = self.search(query, limit, include_kinds=RECALL_MEMORY_KINDS)
        if not results:
            return "No relevant memories found."
        parts = []
        for i, r in enumerate(results, 1):
            parts.append(f"[{i}] {r['title']}\n{r['body'][:300]}")
        return "\n\n".join(parts)

    def recall_with_budget(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.25,
        max_tokens: int = 800,
        session_id: str | None = None,
        decay_rate: float = 0.0,
        retrieval_temperature: float = 0.0,
        scopes: list[str] | None = None,
        max_age_days: int = 0,
        exclude_types: set[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> str:
        """
        Token-budget-aware recall for LLM injection.

        FTS-first: if FTS scores are high, skips vector search.
        Score-gated: skips results below min_score.
        Budget-capped: stops injection at max_tokens (approx 1 token ≈ 4 chars).
        Session-cached: same query within same session cached for 30s.
        decay_rate: exponential time-decay λ; score × e^(-λ × age_days). 0.0 = no decay.
        retrieval_temperature: softmax temperature for random sampling. 0.0 = deterministic top-k.
        scopes: list of scope values to filter (e.g. ["global", "agent:researcher"]).
                None = no filter (all scopes returned).
        max_age_days: drop notes older than N days from recall. 0 = no limit.
        """
        # Cache key includes creativity params and scopes so different modes don't collide
        cache_key = (session_id or "__global__", query, decay_rate, retrieval_temperature,
                     tuple(sorted(scopes)) if scopes else None, max_age_days,
                     tuple(sorted(exclude_types)) if exclude_types else None,
                     tuple(sorted(include_kinds)) if include_kinds else None)
        cached = self._recall_cache.get(cache_key)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        # Internal system notes are never surfaced as recalled memories.
        # _compact_archive: raw conversation dumps (huge, noisy).
        _exclude = sorted(SYSTEM_MEMORY_TAGS)
        recall_kinds = include_kinds if include_kinds is not None else RECALL_MEMORY_KINDS

        # Empty query = serendipity random sampling from all notes
        if not query.strip():
            merged = self._random_sample_notes(limit * 2, scopes=scopes, include_kinds=recall_kinds)
        else:
            # FTS-first strategy
            fts_results = self._fts.search(query, limit * 2, scopes=scopes,
                                           exclude_tags=_exclude)
            fts_max = max((r.get("score_fts", 0.0) for r in fts_results), default=0.0)

            if fts_results and fts_max >= _FTS_SHORTCUT_THRESHOLD:
                # FTS is confident enough — skip vector search to save cost
                merged = [
                    {
                        "note_id": r["note_id"],
                        "title": r.get("title", ""),
                        "body": r.get("body", ""),
                        "created": r.get("created"),
                        "score": self.fts_weight * r.get("score_fts", 0.0),
                    }
                    for r in fts_results
                ]
            else:
                # Full hybrid: FTS + vector
                vec_results = {r["note_id"]: r for r in self._vec.search(query, limit * 2, scopes=scopes,
                                                                          exclude_tags=_exclude)}
                fts_map = {r["note_id"]: r for r in fts_results}
                all_ids = set(fts_map) | set(vec_results)
                merged = []
                for nid in all_ids:
                    fts_s = fts_map.get(nid, {}).get("score_fts", 0.0)
                    vec_s = vec_results.get(nid, {}).get("score_vec", 0.0)
                    combined = self.fts_weight * fts_s + self.vec_weight * vec_s
                    note = fts_map.get(nid) or vec_results.get(nid, {})
                    merged.append({
                        "note_id": nid,
                        "title": note.get("title", ""),
                        "body": note.get("body", ""),
                        "created": note.get("created"),
                        "score": combined,
                    })

        # Apply time-decay penalty and max_age_days filter
        if decay_rate > 0.0 or max_age_days > 0:
            now_ts = time.time()
            cutoff_ts = (now_ts - max_age_days * 86400.0) if max_age_days > 0 else 0.0
            kept = []
            for r in merged:
                created = r.get("created") or now_ts
                if max_age_days > 0 and created < cutoff_ts:
                    continue  # too old — drop from recall pool
                if decay_rate > 0.0:
                    age_days = (now_ts - created) / 86400.0
                    r["score"] = r["score"] * math.exp(-decay_rate * age_days)
                kept.append(r)
            merged = kept

        # recall_count boost and note_type/kind filter/boost — single batch DB query.
        _TYPE_BOOST = {"interest": 1.10, "belief": 1.10, "preference": 1.10}
        if merged:
            rc_map = self._fetch_note_metadata([r["note_id"] for r in merged])
            kept_after_type = []
            for r in merged:
                rc, note_type, memory_kind = rc_map.get(r["note_id"], (0, "fact", "project_knowledge"))
                # Exclude blocked types (e.g. action_log)
                if exclude_types and note_type in exclude_types:
                    continue
                if recall_kinds and memory_kind not in recall_kinds:
                    continue
                if rc > 0:
                    r["score"] = r["score"] * (1.0 + 0.1 * math.log1p(rc))
                # Boost user-modeling types
                type_mult = _TYPE_BOOST.get(note_type, 1.0)
                if type_mult != 1.0:
                    r["score"] = r["score"] * type_mult
                r["note_type"] = note_type
                r["memory_kind"] = memory_kind
                kept_after_type.append(r)
            merged = kept_after_type

        # Score gate
        filtered = [r for r in merged if r["score"] >= min_score]

        # Sort or softmax-weighted random sample
        if retrieval_temperature > 0.0 and len(filtered) > 1:
            import bisect
            temp = max(retrieval_temperature, 1e-6)
            weights = [math.exp(r["score"] / temp) for r in filtered]
            k = min(limit, len(filtered))
            # Build cumulative sum once — O(n); sample with bisect — O(k log n)
            cumsum: list[float] = []
            running = 0.0
            for w in weights:
                running += w
                cumsum.append(running)
            total_w = running
            chosen_indices: set[int] = set()
            chosen: list[dict] = []
            attempts = 0
            while len(chosen) < k and attempts < k * 4:
                attempts += 1
                roll = random.random() * total_w
                idx = bisect.bisect_left(cumsum, roll)
                idx = min(idx, len(filtered) - 1)
                if idx not in chosen_indices:
                    chosen_indices.add(idx)
                    chosen.append(filtered[idx])
            # Fill any remaining slots with highest-scoring unselected items
            if len(chosen) < k:
                for i, r in enumerate(sorted(range(len(filtered)), key=lambda i: filtered[i]["score"], reverse=True)):
                    if r not in chosen_indices and len(chosen) < k:
                        chosen.append(filtered[r])
            filtered = chosen
        else:
            filtered = sorted(filtered, key=lambda r: r["score"], reverse=True)

        if not filtered:
            result = ""
        else:
            # Budget cap (approx 1 token ≈ 4 chars)
            parts: list[str] = []
            recalled_ids: list[str] = []
            total_tokens = 0
            for r in filtered[:limit]:
                body = r["body"][:300]
                entry = f"[{r['title']}]\n{body}"
                entry_tokens = max(1, len(entry) // 4)
                if max_tokens > 0 and (total_tokens + entry_tokens > max_tokens):
                    break
                parts.append(entry)
                recalled_ids.append(r["note_id"])
                total_tokens += entry_tokens
            result = "\n\n".join(parts)

            # Increment recall_count for notes that actually appeared in the output.
            # Batched update; ignore errors (e.g. note deleted mid-flight).
            if recalled_ids:
                placeholders = ",".join("?" * len(recalled_ids))
                try:
                    self.conn.execute(
                        f"UPDATE notes SET recall_count = recall_count + 1 "
                        f"WHERE note_id IN ({placeholders})",
                        recalled_ids,
                    )
                    self.conn.commit()
                except Exception:
                    pass

        # Update session cache and evict stale entries
        now = time.time()
        self._recall_cache[cache_key] = (result, now)
        stale = [k for k, v in self._recall_cache.items() if now - v[1] >= _CACHE_TTL]
        for k in stale:
            del self._recall_cache[k]

        return result

    def _random_sample_notes(
        self,
        limit: int,
        scopes: list[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        """Return random notes for serendipity injection. All get score=1.0."""
        clauses: list[str] = []
        params: list[object] = []
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            clauses.append(f"n.scope IN ({placeholders})")
            params.extend(scopes)
        if include_kinds:
            placeholders = ",".join("?" * len(include_kinds))
            clauses.append(f"n.memory_kind IN ({placeholders})")
            params.extend(sorted(include_kinds))
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        count_row = self.conn.execute(
            f"SELECT COUNT(*) FROM notes n {where_sql}", params
        ).fetchone()
        total = count_row[0] if count_row else 0
        if total == 0:
            return []
        k = min(limit, total)
        offsets = sorted(random.sample(range(total), k))
        rows = []
        for off in offsets:
            row = self.conn.execute(
                f"SELECT n.note_id, n.title, n.created, n.note_type, n.memory_kind, b.body "
                f"FROM notes n JOIN note_bodies b USING(note_id) "
                f"{where_sql}LIMIT 1 OFFSET ?",
                (*params, off),
            ).fetchone()
            if row:
                rows.append(row)
        return [
            {
                "note_id": r["note_id"],
                "title": r["title"] or "",
                "body": r["body"] or "",
                "created": r["created"],
                "note_type": r["note_type"] or "fact",
                "memory_kind": r["memory_kind"] or "project_knowledge",
                "score": 1.0,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Runtime projections
    # ------------------------------------------------------------------

    def freeze_tool_surface(self, session_id: str, candidate: dict) -> dict:
        """Persist once and return the canonical provider tool surface.

        ``INSERT OR IGNORE`` makes concurrent loop creation safe: the first
        complete snapshot wins and every later resume reads the same bytes.
        """
        sid = str(session_id or "").strip()
        schemas = candidate.get("schemas") if isinstance(candidate, dict) else None
        if not sid or not isinstance(schemas, list):
            return {}
        now = int(time.time() * 1000)
        self.conn.execute(
            "INSERT OR IGNORE INTO session_tool_surfaces "
            "(session_id, mode, schemas_json, fingerprint, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                sid,
                str(candidate.get("mode") or "all"),
                json.dumps(schemas, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                str(candidate.get("fingerprint") or ""),
                now,
                now,
            ),
        )
        row = self.conn.execute(
            "SELECT mode, schemas_json, fingerprint, created, updated "
            "FROM session_tool_surfaces WHERE session_id=?",
            (sid,),
        ).fetchone()
        if row is None:
            return {}
        try:
            frozen_schemas = json.loads(row["schemas_json"] or "[]")
        except Exception:
            return {}
        return {
            "mode": str(row["mode"] or "all"),
            "schemas": frozen_schemas if isinstance(frozen_schemas, list) else [],
            "fingerprint": str(row["fingerprint"] or ""),
            "created": int(row["created"] or 0),
            "updated": int(row["updated"] or 0),
        }

    def list_run_metrics(
        self,
        *,
        session_id: str = "",
        run_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        """Return recent query-friendly per-run latency envelopes.

        ``events`` remains the source of truth.  This reader intentionally
        depends on the disposable ``run_metrics`` projection so dashboards and
        diagnostics do not repeatedly scan and decode the append-only log.
        """
        clauses: list[str] = []
        params: list[object] = []
        if session_id:
            clauses.append("session_id=?")
            params.append(str(session_id))
        if run_id:
            clauses.append("run_id=?")
            params.append(str(run_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit or 100), 1_000)))
        rows = self.conn.execute(
            "SELECT metric_id, event_id, run_id, thread_id, session_id, model, "
            "tool_surface_mode, tool_registry_count, tool_visible_count, "
            "tool_schema_tokens, assemble_ms, ttft_ms, llm_ms, tool_ms, "
            "persist_ms, total_ms, perf_json, created "
            f"FROM run_metrics {where} ORDER BY created DESC, event_id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["perf"] = json.loads(item.pop("perf_json") or "{}")
            except Exception:
                item.pop("perf_json", None)
                item["perf"] = {}
            result.append(item)
        return result

    # Session / Turn persistence
    # ------------------------------------------------------------------

    def _backfill_sessions(self) -> None:
        """Populate session metadata rows for DBs created before session tracking."""
        rows = self.conn.execute(
            "SELECT session, MIN(ts) AS created, MAX(ts) AS last_turn, COUNT(*) AS turn_count, "
            "SUM(input_tokens) AS total_input_tokens, SUM(output_tokens) AS total_output_tokens, "
            "MAX(workspace) AS workspace "
            "FROM turns GROUP BY session"
        ).fetchall()
        for row in rows:
            session_id = str(row["session"] or "")
            if not session_id:
                continue
            kind = self._session_kind(session_id)
            first_user = self._first_user_message(session_id)
            last_user = self._last_user_message(session_id)
            last_content = self._last_turn_content(session_id)
            title = self._derive_session_title(first_user) or self._fallback_title(session_id)
            self.conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(session_id, kind, title, workspace, first_user_message, last_user_message, last_preview, "
                "created, updated, last_turn, turn_count, total_input_tokens, total_output_tokens) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    kind,
                    title,
                    str(row["workspace"] or ""),
                    first_user,
                    last_user,
                    self._clip_text(last_user or last_content, 72),
                    int(row["created"] or int(time.time())),
                    int(row["last_turn"] or int(time.time())),
                    int(row["last_turn"] or 0),
                    int(row["turn_count"] or 0),
                    int(row["total_input_tokens"] or 0),
                    int(row["total_output_tokens"] or 0),
                ),
            )
        self._backfill_session_cache_fields()
        self.conn.commit()

    def _backfill_turns_fts(self) -> None:
        """Index persisted turns for cross-session full-text search."""
        self.conn.execute(
            "INSERT INTO turns_fts(rowid, turn_id, session, role, content) "
            "SELECT rowid, turn_id, session, role, content FROM turns "
            "WHERE turn_id NOT IN (SELECT turn_id FROM turns_fts)"
        )
        self.conn.commit()

    def _first_user_message(self, session_id: str) -> str:
        row = self.conn.execute(
            "SELECT content FROM turns WHERE session=? AND role='user' ORDER BY ts ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return ""
        return str(row["content"] or "")

    def _first_user_title(self, session_id: str) -> str:
        return self._clip_text(self._first_user_message(session_id), 56)

    def _last_user_message(self, session_id: str) -> str:
        row = self.conn.execute(
            "SELECT content FROM turns WHERE session=? AND role='user' ORDER BY ts DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return ""
        return str(row["content"] or "")

    def _last_turn_content(self, session_id: str) -> str:
        row = self.conn.execute(
            "SELECT content FROM turns WHERE session=? ORDER BY ts DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return ""
        return str(row["content"] or "")

    def _backfill_session_cache_fields(self) -> None:
        rows = self.conn.execute(
            "SELECT session_id, first_user_message, last_user_message, last_preview, "
            "turn_count, total_input_tokens, total_output_tokens FROM sessions"
        ).fetchall()
        for row in rows:
            session_id = str(row["session_id"] or "")
            if not session_id:
                continue
            first_user = str(row["first_user_message"] or "")
            last_user = str(row["last_user_message"] or "")
            last_preview = str(row["last_preview"] or "")
            turn_count = int(row["turn_count"] or 0)
            total_input_tokens = int(row["total_input_tokens"] or 0)
            total_output_tokens = int(row["total_output_tokens"] or 0)
            if turn_count <= 0:
                continue
            if first_user and last_user and last_preview and (total_input_tokens or total_output_tokens):
                continue
            computed_first = first_user or self._first_user_message(session_id)
            computed_last = last_user or self._last_user_message(session_id)
            computed_last_content = self._last_turn_content(session_id)
            totals = self.conn.execute(
                "SELECT SUM(input_tokens) AS total_input_tokens, SUM(output_tokens) AS total_output_tokens "
                "FROM turns WHERE session=?",
                (session_id,),
            ).fetchone()
            self.conn.execute(
                "UPDATE sessions SET first_user_message=?, last_user_message=?, last_preview=?, "
                "total_input_tokens=?, total_output_tokens=? WHERE session_id=?",
                (
                    computed_first,
                    computed_last,
                    last_preview or self._clip_text(computed_last or computed_last_content, 72),
                    total_input_tokens or int(totals["total_input_tokens"] or 0),
                    total_output_tokens or int(totals["total_output_tokens"] or 0),
                    session_id,
                ),
            )

    def _upsert_session(
        self,
        session_id: str,
        *,
        created: int | None = None,
        updated: int | None = None,
        last_turn: int | None = None,
        workspace: str = "",
        source: str = "",
        parent_session_id: str = "",
        title: str = "",
    ) -> None:
        now = int(time.time())
        created_ts = created if created is not None else now
        updated_ts = updated if updated is not None else created_ts
        last_turn_ts = last_turn if last_turn is not None else updated_ts
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, parent_session_id, source, kind, title, workspace, first_user_message, last_user_message, "
            "last_preview, created, updated, last_turn, turn_count, total_input_tokens, total_output_tokens) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,0)",
            (
                session_id,
                parent_session_id or "",
                source or "",
                self._session_kind(session_id),
                title or self._fallback_title(session_id),
                workspace or "",
                "",
                "",
                "",
                created_ts,
                updated_ts,
                last_turn_ts,
            ),
        )

    def annotate_session(
        self,
        session_id: str,
        *,
        source: str = "",
        workspace: str = "",
        parent_session_id: str = "",
        title: str = "",
    ) -> None:
        """Update session metadata that may arrive outside raw turn persistence."""
        topic_title = self._derive_session_title(title)
        self._upsert_session(
            session_id,
            workspace=workspace,
            source=source,
            parent_session_id=parent_session_id,
            title=topic_title or self._fallback_title(session_id),
        )
        row = self.conn.execute(
            "SELECT title, title_source, source, workspace, parent_session_id FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        existing_title = str(row["title"] or "")
        title_source = str(row["title_source"] or "")
        next_title = existing_title
        next_title_source = title_source
        if title_source in _SESSION_TITLE_LOCKED_SOURCES:
            next_title = existing_title
        elif topic_title and self._is_fallback_title(session_id, existing_title):
            next_title = topic_title
            next_title_source = _SESSION_TITLE_SOURCE_AUTO_LOCAL
        self.conn.execute(
            "UPDATE sessions SET source=?, workspace=?, parent_session_id=?, title=?, title_source=?, updated=? "
            "WHERE session_id=?",
            (
                source or str(row["source"] or ""),
                workspace or str(row["workspace"] or ""),
                parent_session_id or str(row["parent_session_id"] or ""),
                next_title or self._fallback_title(session_id),
                next_title_source,
                int(time.time()),
                session_id,
            ),
        )
        self.conn.commit()

    @classmethod
    def _normalize_session_title(cls, title: str) -> str:
        return cls._clip_text(re.sub(r"\s+", " ", str(title or "")).strip(), _SESSION_TITLE_MAX)

    def rename_session(self, session_id: str, title: str) -> dict:
        """Persist a user-managed title for a session."""
        sid = str(session_id or "").strip()
        next_title = self._normalize_session_title(title)
        if not sid:
            return {"ok": False, "error": "session_id is required"}
        if not next_title:
            return {"ok": False, "error": "Session title is required"}
        row = self.conn.execute(
            "SELECT session_id FROM sessions WHERE session_id=?",
            (sid,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "Session not found"}
        now = int(time.time())
        self.conn.execute(
            "UPDATE sessions SET title=?, title_source=?, updated=? WHERE session_id=?",
            (next_title, _SESSION_TITLE_SOURCE_MANUAL, now, sid),
        )
        self.conn.commit()
        return {"ok": True, "session_id": sid, "title": next_title, "title_source": _SESSION_TITLE_SOURCE_MANUAL}

    def should_generate_llm_session_title(self, session_id: str, user_input: str = "") -> bool:
        sid = str(session_id or "").strip()
        if not sid or self._session_kind(sid) != "chat":
            return False
        row = self.conn.execute(
            "SELECT title, title_source, first_user_message, last_user_message "
            "FROM sessions WHERE session_id=?",
            (sid,),
        ).fetchone()
        if row is None:
            return False
        title_source = str(row["title_source"] or "")
        if title_source in _SESSION_TITLE_LOCKED_SOURCES:
            return False
        first_user = " ".join(str(row["first_user_message"] or "").split()).strip()
        last_user = " ".join(str(row["last_user_message"] or "").split()).strip()
        expected = " ".join(str(user_input or "").split()).strip()
        if not first_user or not last_user:
            return False
        if expected and expected != first_user:
            return False
        if first_user != last_user:
            return False
        current_title = str(row["title"] or "")
        first_topic = self._derive_session_title(first_user)
        allowed_titles = {
            "",
            self._fallback_title(sid),
            first_topic,
            self._clip_text(first_user, 56),
        }
        return current_title in allowed_titles

    def save_generated_session_title(self, session_id: str, title: str) -> dict:
        sid = str(session_id or "").strip()
        next_title = self._normalize_session_title(title)
        if not sid:
            return {"ok": False, "error": "session_id is required"}
        if not next_title:
            return {"ok": False, "error": "Session title is required"}
        now = int(time.time())
        cur = self.conn.execute(
            "UPDATE sessions SET title=?, title_source=?, updated=? "
            "WHERE session_id=? AND COALESCE(title_source, '') NOT IN (?, ?)",
            (
                next_title,
                _SESSION_TITLE_SOURCE_AUTO_LLM,
                now,
                sid,
                _SESSION_TITLE_SOURCE_MANUAL,
                _SESSION_TITLE_SOURCE_AUTO_LLM,
            ),
        )
        self.conn.commit()
        if int(getattr(cur, "rowcount", 0) or 0) <= 0:
            row = self.conn.execute(
                "SELECT title, title_source FROM sessions WHERE session_id=?",
                (sid,),
            ).fetchone()
            return {
                "ok": False,
                "session_id": sid,
                "title": str(row["title"] or "") if row is not None else "",
                "title_source": str(row["title_source"] or "") if row is not None else "",
                "updated": False,
            }
        return {
            "ok": True,
            "session_id": sid,
            "title": next_title,
            "title_source": _SESSION_TITLE_SOURCE_AUTO_LLM,
            "updated": True,
        }

    def move_session_workspace(self, session_id: str, workspace: str) -> None:
        now = int(time.time())
        self.conn.execute(
            "UPDATE sessions SET workspace=?, updated=? WHERE session_id=?",
            (workspace, now, session_id),
        )
        self.conn.execute(
            "UPDATE turns SET workspace=? WHERE session=?",
            (workspace, session_id),
        )
        self.conn.commit()

    def record_session_compaction(
        self,
        session_id: str,
        *,
        archived: int,
        kept: int,
        parent_session_id: str = "",
    ) -> str:
        """Persist a lineage event for a compaction boundary."""
        now = int(time.time())
        self._upsert_session(session_id, updated=now, last_turn=now)
        lineage_id = make_id("lin-")
        summary_path = str((self.sessions_dir / session_id / "summary.md").resolve())
        meta = {
            "archived": archived,
            "kept": kept,
            "summary_path": summary_path,
        }
        self.conn.execute(
            "INSERT INTO session_lineage (lineage_id, session_id, parent_session_id, relationship, ts, meta_json) "
            "VALUES (?,?,?,?,?,?)",
            (
                lineage_id,
                session_id,
                parent_session_id or session_id,
                "compacted",
                now,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        self.conn.execute(
            "UPDATE sessions SET compaction_count=compaction_count+1, "
            "last_compacted_at=?, updated=? WHERE session_id=?",
            (now, now, session_id),
        )
        self.conn.commit()
        return lineage_id

    def get_session_lineage(self, session_id: str) -> list[dict]:
        """Return lineage events for a session, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM session_lineage WHERE session_id=? ORDER BY ts DESC",
            (session_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["meta_json"] = json.loads(item.get("meta_json") or "{}")
            except Exception:
                item["meta_json"] = {}
            out.append(item)
        return out

    def search_sessions(
        self,
        query: str,
        limit: int = 20,
        include_scheduled: bool = True,
        workspace: str | None = None,
    ) -> list[dict]:
        """Search persisted conversation turns across sessions."""
        q = (query or "").strip()
        if not q:
            return []
        safe_q = _build_fts_query(q)
        rows = []
        if safe_q:
            where = ["turns_fts MATCH ?"]
            params: list[object] = [safe_q]
            if not include_scheduled:
                where.append("t.session NOT LIKE 'sched_%'")
            if workspace is not None:
                where.append("COALESCE(s.workspace, '') = ?")
                params.append(workspace)
            sql = (
                "SELECT t.session AS session_id, t.turn_id, t.role, t.content, t.ts, "
                "bm25(turns_fts) AS score, "
                "snippet(turns_fts, 3, '[', ']', '…', 12) AS snippet, "
                "COALESCE(s.title, '') AS title, COALESCE(s.title_source, '') AS title_source, "
                "COALESCE(s.kind, '') AS kind, "
                "COALESCE(s.first_user_message, '') AS first_user_message, "
                "COALESCE(s.last_user_message, '') AS last_user_message, "
                "COALESCE(s.last_preview, '') AS last_preview, "
                "COALESCE(s.parent_session_id, '') AS parent_session_id, "
                "COALESCE(s.source, '') AS source, COALESCE(s.compaction_count, 0) AS compaction_count "
                "FROM turns_fts "
                "JOIN turns t ON t.rowid = turns_fts.rowid "
                "LEFT JOIN sessions s ON s.session_id = t.session "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY score, t.ts DESC LIMIT ?"
            )
            params.append(max(1, int(limit)))
            try:
                rows = self.conn.execute(sql, tuple(params)).fetchall()
            except OPERATIONAL_ERRORS:
                safe_q = " ".join(f'"{w.replace(chr(34), chr(34) * 2)}"' for w in q.split() if w)
                if safe_q:
                    params[0] = safe_q
                    try:
                        rows = self.conn.execute(sql, tuple(params)).fetchall()
                    except OPERATIONAL_ERRORS:
                        rows = []
        title_rows = []
        if re.search(r"[\w\u4e00-\u9fff]", q, re.UNICODE):
            title_where = ["s.title LIKE ?"]
            title_params: list[object] = [f"%{q}%"]
            if not include_scheduled:
                title_where.append("s.session_id NOT LIKE 'sched_%'")
            if workspace is not None:
                title_where.append("COALESCE(s.workspace, '') = ?")
                title_params.append(workspace)
            title_sql = (
                "SELECT s.session_id AS session_id, '' AS turn_id, '' AS role, '' AS content, "
                "s.last_turn AS ts, -1000.0 AS score, '' AS snippet, "
                "COALESCE(s.title, '') AS title, COALESCE(s.title_source, '') AS title_source, "
                "COALESCE(s.kind, '') AS kind, "
                "COALESCE(s.first_user_message, '') AS first_user_message, "
                "COALESCE(s.last_user_message, '') AS last_user_message, "
                "COALESCE(s.last_preview, '') AS last_preview, "
                "COALESCE(s.parent_session_id, '') AS parent_session_id, "
                "COALESCE(s.source, '') AS source, COALESCE(s.compaction_count, 0) AS compaction_count "
                "FROM sessions s "
                f"WHERE {' AND '.join(title_where)} "
                "ORDER BY s.last_turn DESC LIMIT ?"
            )
            title_params.append(max(1, int(limit)))
            title_rows = self.conn.execute(title_sql, tuple(title_params)).fetchall()
        out = []
        seen_sessions: set[str] = set()
        for row in [*title_rows, *rows]:
            d = self._resolve_session_title_and_preview(dict(row))
            session_id = str(d.get("session_id") or "")
            if not session_id or session_id in seen_sessions:
                continue
            seen_sessions.add(session_id)
            d.pop("title_source", None)
            if len(out) >= max(1, int(limit)):
                break
            out.append(d)
        return out

    def save_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        workspace: str = "",
    ) -> str:
        turn_id = make_id()
        now = int(time.time())
        self._upsert_session(
            session_id,
            created=now,
            updated=now,
            last_turn=now,
            workspace=workspace or "",
            title=self._fallback_title(session_id),
        )
        self.conn.execute(
            "INSERT INTO turns (turn_id, session, role, content, tool_name, ts, "
            "input_tokens, output_tokens, workspace) VALUES (?,?,?,?,?,?,?,?,?)",
            (turn_id, session_id, role, content, tool_name, now, input_tokens, output_tokens, workspace or ""),
        )
        self.conn.execute(
            "INSERT INTO turns_fts(rowid, turn_id, session, role, content) VALUES (last_insert_rowid(), ?, ?, ?, ?)",
            (turn_id, session_id, role, content),
        )
        current_title = self._derive_session_title(content) if role == "user" else ""
        preview = self._clip_text(content, 72)
        self.conn.execute(
            "UPDATE sessions SET updated=?, last_turn=?, turn_count=turn_count+1, "
            "total_input_tokens=total_input_tokens+?, total_output_tokens=total_output_tokens+?, "
            "workspace=CASE WHEN ? != '' THEN ? ELSE workspace END, "
            "first_user_message=CASE WHEN ?='user' AND COALESCE(first_user_message, '')='' THEN ? ELSE first_user_message END, "
            "last_user_message=CASE WHEN ?='user' THEN ? ELSE last_user_message END, "
            "last_preview=CASE WHEN ?='user' THEN ? "
            "WHEN COALESCE(last_user_message, '')='' THEN ? ELSE last_preview END, "
            "title=CASE WHEN (title='' OR title=?) AND ? != '' THEN ? ELSE title END, "
            "title_source=CASE "
            "WHEN COALESCE(title_source, '') IN (?, ?) THEN title_source "
            "WHEN ?='user' AND (title='' OR title=?) AND ? != '' THEN ? "
            "ELSE title_source END "
            "WHERE session_id=?",
            (
                now,
                now,
                input_tokens,
                output_tokens,
                workspace or "",
                workspace or "",
                role,
                content if role == "user" else "",
                role,
                content if role == "user" else "",
                role,
                preview,
                preview,
                self._fallback_title(session_id),
                current_title,
                current_title,
                _SESSION_TITLE_SOURCE_MANUAL,
                _SESSION_TITLE_SOURCE_AUTO_LLM,
                role,
                self._fallback_title(session_id),
                current_title,
                _SESSION_TITLE_SOURCE_AUTO_LOCAL,
                session_id,
            ),
        )
        self.conn.commit()
        # Also append to .jsonl file for human-readable history
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        turn_file = session_dir / "turns.jsonl"
        with open(turn_file, "a", encoding="utf-8") as f:
            record = {
                "turn_id": turn_id, "role": role, "content": content,
                "tool_name": tool_name, "ts": now,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return turn_id

    def update_turn_tokens(self, turn_id: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        row = self.conn.execute(
            "SELECT session, input_tokens, output_tokens FROM turns WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
        if row is None:
            return
        self.conn.execute(
            "UPDATE turns SET input_tokens=?, output_tokens=? WHERE turn_id=?",
            (input_tokens, output_tokens, turn_id),
        )
        self.conn.execute(
            "UPDATE sessions SET total_input_tokens=total_input_tokens+?, "
            "total_output_tokens=total_output_tokens+? WHERE session_id=?",
            (
                int(input_tokens) - int(row["input_tokens"] or 0),
                int(output_tokens) - int(row["output_tokens"] or 0),
                str(row["session"] or ""),
            ),
        )
        self.conn.commit()

    async def asave_turn(self, *args, **kwargs) -> str:
        """Non-blocking save_turn: runs in a thread-pool worker."""
        _lock = _conn_lock(self.conn)
        def _do() -> str:
            with _lock:
                return self.save_turn(*args, **kwargs)
        return await asyncio.to_thread(_do)

    # ------------------------------------------------------------------
    # Thread / Run management (Phase 3)
    # ------------------------------------------------------------------

    def get_or_create_thread(
        self,
        session_id: str,
        *,
        agent_name: str = "",
        parent_thread_id: str = "",
    ) -> str:
        """Return existing primary thread for session, or create one.

        For sub-agents, pass parent_thread_id to record the delegation chain.
        """
        now = int(time.time())
        row = self.conn.execute(
            "SELECT thread_id FROM threads "
            "WHERE session_id=? AND agent_name=? AND parent_thread_id='' LIMIT 1",
            (session_id, agent_name),
        ).fetchone()
        if row:
            return row["thread_id"]
        tid = make_id("th-")
        self.conn.execute(
            "INSERT INTO threads (thread_id, session_id, parent_thread_id, agent_name, status, created, updated) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (tid, session_id, parent_thread_id, agent_name, now, now),
        )
        self.conn.commit()
        return tid

    def create_child_thread(
        self,
        session_id: str,
        parent_thread_id: str,
        *,
        agent_name: str = "",
    ) -> str:
        """Create a child thread for a sub-agent delegation."""
        now = int(time.time())
        tid = make_id("th-")
        self.conn.execute(
            "INSERT INTO threads (thread_id, session_id, parent_thread_id, agent_name, status, created, updated) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (tid, session_id, parent_thread_id, agent_name, now, now),
        )
        self.conn.commit()
        return tid

    def create_run(
        self,
        thread_id: str,
        session_id: str,
        *,
        parent_run_id: str = "",
        trigger_type: str = "user",
        run_kind: str = "primary",
        visibility: str = "foreground",
    ) -> str:
        """Open a new run within a thread. Returns run_id."""
        now = int(time.time())
        rid = make_id("run-")
        self.conn.execute(
            "INSERT INTO runs (run_id, thread_id, session_id, parent_run_id, trigger_type, run_kind, visibility, status, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (rid, thread_id, session_id, parent_run_id, trigger_type, run_kind, visibility, now, now),
        )
        self.conn.commit()
        return rid

    def _update_run_status(self, run_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE runs SET status=?, updated=? WHERE run_id=?",
            (status, int(time.time()), run_id),
        )
        self.conn.commit()

    def complete_run(self, run_id: str) -> None:
        self._update_run_status(run_id, "completed")

    def fail_run(self, run_id: str) -> None:
        self._update_run_status(run_id, "failed")

    def load_session_turns(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM turns WHERE session=? ORDER BY ts",
            (session_id,),
        ).fetchall()
        turns = []
        for r in rows:
            item = dict(r)
            item["message_id"] = f"turn:{item.get('turn_id', '')}"
            turns.append(item)
        return turns

    def load_session_history(self, session_id: str) -> list[dict]:
        """Return session transcript with event replay preferred over turns.

        The append-only event log is the source of truth. The legacy ``turns``
        table remains available as a compatibility cache and fallback for older
        sessions that predate replayable event payloads.
        """
        replayed = self.session_log.replay_turns(session_id=session_id)
        if replayed:
            return self._materialize_message_references(
                self._apply_message_states(replayed, include_hidden=False),
                session_id=session_id,
            )
        return self._materialize_message_references(
            self._apply_message_states(self.load_session_turns(session_id), include_hidden=False),
            session_id=session_id,
        )

    def load_thread_history(self, thread_id: str) -> list[dict]:
        """Return thread-scoped transcript, falling back to session history."""
        replayed = self.session_log.replay_turns(thread_id=thread_id)
        if replayed:
            session_id = str(replayed[0].get("session_id") or "") if replayed else ""
            return self._materialize_message_references(
                self._apply_message_states(replayed, include_hidden=False),
                session_id=session_id,
            )
        row = self.conn.execute(
            "SELECT session_id FROM threads WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if row is None:
            return []
        return self.load_session_history(str(row["session_id"]))

    @staticmethod
    def _reference_preview_text(text: str, *, limit: int = 96) -> str:
        preview = " ".join(str(text or "").split())
        if len(preview) <= limit:
            return preview
        return f"{preview[: max(0, limit - 1)].rstrip()}…"

    def _materialize_message_references(self, turns: list[dict], *, session_id: str) -> list[dict]:
        out: list[dict] = []
        for turn in turns:
            item = dict(turn)
            refs = item.get("references") or []
            if not isinstance(refs, list) or not refs:
                item.pop("references", None)
                out.append(item)
                continue
            resolved_refs: list[dict] = []
            for ref in refs[:5]:
                if not isinstance(ref, dict):
                    continue
                mid = str(ref.get("message_id") or "").strip()
                if not mid:
                    continue
                resolved = self.resolve_message_ref(mid, session_id=session_id)
                if not resolved:
                    continue
                preview = self._reference_preview_text(str(resolved.get("content") or ""))
                if not preview:
                    continue
                resolved_refs.append({
                    "message_id": str(resolved.get("message_id") or mid),
                    "role": str(resolved.get("role") or ""),
                    "preview": preview,
                })
            if resolved_refs:
                item["references"] = resolved_refs
            else:
                item.pop("references", None)
            out.append(item)
        return out

    def _message_state_map(self, message_ids: list[str]) -> dict[str, dict]:
        ids = [m for m in message_ids if m]
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT message_id, hidden, excluded, purged FROM message_states "
            f"WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        return {
            r["message_id"]: {
                "hidden": bool(r["hidden"]),
                "excluded": bool(r["excluded"]),
                "purged": bool(r["purged"]),
            }
            for r in rows
        }

    def _apply_message_states(self, turns: list[dict], *, include_hidden: bool) -> list[dict]:
        states = self._message_state_map([str(t.get("message_id") or "") for t in turns])
        out: list[dict] = []
        for t in turns:
            item = dict(t)
            state = states.get(str(item.get("message_id") or ""), {})
            item["hidden"] = bool(state.get("hidden", False))
            item["excluded"] = bool(state.get("excluded", False))
            item["purged"] = bool(state.get("purged", False))
            if item["purged"]:
                continue
            if item["hidden"] and not include_hidden:
                continue
            out.append(item)
        return out

    def _canonical_message_id(self, message_id: str) -> str:
        mid = str(message_id or "").strip()
        if not mid:
            return ""
        if mid.startswith("turn:") or mid.startswith("event:"):
            return mid
        if self.conn.execute("SELECT 1 FROM turns WHERE turn_id=?", (mid,)).fetchone():
            return f"turn:{mid}"
        if self.conn.execute("SELECT 1 FROM events WHERE event_id=?", (mid,)).fetchone():
            return f"event:{mid}"
        return mid

    def canonical_message_id(self, message_id: str) -> str:
        """Return the stable opaque message id used by public UI/API payloads."""
        return self._canonical_message_id(message_id)

    def resolve_message_ref(self, message_id: str, *, session_id: str = "") -> dict | None:
        """Resolve an opaque message_id into transcript content for explicit references."""
        mid = self._canonical_message_id(message_id)
        if not mid:
            return None

        _state_row = self.conn.execute(
            "SELECT hidden, excluded, purged FROM message_states WHERE message_id=?", (mid,)
        ).fetchone()
        state = {
            "hidden": bool(_state_row["hidden"]),
            "excluded": bool(_state_row["excluded"]),
            "purged": bool(_state_row["purged"]),
        } if _state_row else {}
        if state.get("purged"):
            return None

        if mid.startswith("turn:"):
            turn_id = mid.split(":", 1)[1]
            row = self.conn.execute(
                "SELECT turn_id, session, role, content, tool_name, ts FROM turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if row is None:
                return None
            if session_id and row["session"] != session_id:
                return None
            return {
                "message_id": mid,
                "session_id": row["session"],
                "role": row["role"],
                "content": row["content"],
                "tool_name": row["tool_name"] or "",
                "ts": int(row["ts"] or 0),
                "hidden": bool(state.get("hidden", False)),
                "excluded": bool(state.get("excluded", False)),
            }

        if mid.startswith("event:"):
            event_id = mid.split(":", 1)[1]
            row = self.conn.execute(
                "SELECT event_id, session_id, type, payload_json, ts FROM events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if row is None:
                return None
            if session_id and row["session_id"] != session_id:
                return None
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
            event_type = row["type"]
            role = ""
            content = ""
            tool_name = ""
            if event_type == "user_message_received":
                role = "user"
                content = str(payload.get("input") or "")
            elif event_type == "assistant_message_emitted":
                role = "assistant"
                content = str(payload.get("text") or "")
            elif event_type == "tool_call_requested":
                role = "tool"
                tool_name = str(payload.get("tool") or "")
                content = str(payload.get("result") or payload.get("error") or "")
            if not role or not content:
                return None
            return {
                "message_id": mid,
                "session_id": row["session_id"],
                "role": role,
                "content": content,
                "tool_name": tool_name,
                "ts": int(row["ts"] or 0),
                "hidden": bool(state.get("hidden", False)),
                "excluded": bool(state.get("excluded", False)),
            }

        return None

    def set_message_state(
        self,
        message_id: str,
        *,
        session_id: str = "",
        hidden: bool | None = None,
        excluded: bool | None = None,
        purged: bool | None = None,
    ) -> bool:
        """Persist user-controlled message state and append an audit event."""
        mid = self._canonical_message_id(message_id)
        if not mid:
            return False
        resolved = self.resolve_message_ref(mid, session_id=session_id)
        if not resolved:
            return False
        sid = str(resolved.get("session_id") or session_id or "").strip()
        if not sid:
            return False

        row = self.conn.execute(
            "SELECT hidden, excluded, purged FROM message_states WHERE message_id=?",
            (mid,),
        ).fetchone()
        current = {
            "hidden": int(row["hidden"]) if row else 0,
            "excluded": int(row["excluded"]) if row else 0,
            "purged": int(row["purged"]) if row else 0,
        }
        if hidden is not None:
            current["hidden"] = 1 if hidden else 0
        if excluded is not None:
            current["excluded"] = 1 if excluded else 0
        if purged is not None:
            current["purged"] = 1 if purged else 0
        now = int(time.time())
        self.conn.execute(
            """INSERT INTO message_states(message_id, session_id, hidden, excluded, purged, updated)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                 session_id=excluded.session_id,
                 hidden=excluded.hidden,
                 excluded=excluded.excluded,
                 purged=excluded.purged,
                 updated=excluded.updated""",
            (mid, sid, current["hidden"], current["excluded"], current["purged"], now),
        )
        self.conn.commit()
        self.session_log.append(
            sid,
            "message_state_changed",
            {
                "message_id": mid,
                "hidden": bool(current["hidden"]),
                "excluded": bool(current["excluded"]),
                "purged": bool(current["purged"]),
            },
        )
        return True

    def delete_message_derived_data(self, message_id: str) -> dict[str, int]:
        mid = self._canonical_message_id(message_id)
        if not mid:
            return {"notes": 0, "profile_facts": 0, "reflections": 0}
        notes = self.delete_notes_by_source_message(mid)
        profile_facts = self.user_profile.delete_facts_by_source_message(mid)
        reflections = self.delete_reflections_by_source_message(mid)
        return {
            "notes": notes,
            "profile_facts": profile_facts,
            "reflections": reflections,
        }

    @staticmethod
    def _clip_text(text: str, limit: int = 56) -> str:
        s = " ".join((text or "").split())
        if not s:
            return ""
        return s if len(s) <= limit else s[:limit].rstrip() + "…"

    @classmethod
    def _derive_session_title(cls, text: str, limit: int = 32) -> str:
        """Return a stable topic-like title from the first meaningful user ask.

        This deliberately stays deterministic and local: session titles should
        be reliable navigation labels, not model-generated prose that changes
        from turn to turn.
        """
        s = str(text or "").strip()
        if not s:
            return ""
        s = re.sub(r"```.*?```", " ", s, flags=re.S)
        s = re.sub(r"`([^`]*)`", r"\1", s)
        s = re.sub(r"https?://\S+", " ", s)
        s = re.sub(r"\s+", " ", s).strip(" \t\r\n\"'“”‘’")
        if not s:
            return ""

        lower = s.lower().strip()
        if lower in {"commit", "push", "commit / push", "commit/push", "落地吧", "继续", "继续吧"}:
            return ""

        # Prefer the first clause: it usually contains the user's topic, while
        # later clauses contain rationale or examples.
        parts = re.split(r"[。！？!?；;\n]|[,，](?=\S)", s, maxsplit=1)
        s = (parts[0] if parts else s).strip()

        prefix_replacements = (
            r"^(请|麻烦|帮我|帮忙|能不能|可以|可否|请你|你帮我|你看下|看下|我们来|我想|我希望)",
            r"^(please|can you|could you|help me|let'?s)\s+",
        )
        for pattern in prefix_replacements:
            s = re.sub(pattern, "", s, flags=re.I).strip()

        cleanup_pairs = (
            ("现在需要", ""),
            ("需要升级下", "升级"),
            ("需要升级一下", "升级"),
            ("现在升级", "升级"),
            ("升级下", "升级"),
            ("优化下", "优化"),
            ("优化一下", "优化"),
            ("处理一下", "处理"),
            ("看一下", ""),
            ("看下", ""),
            ("如何处理", ""),
            ("应该如何处理", ""),
            ("怎么办", ""),
        )
        for old, new in cleanup_pairs:
            s = s.replace(old, new)
        s = s.strip(" ：:，,。.!！？?、-—")
        if not s:
            return ""

        # Keep labels compact enough for the sidebar but less lossy than the
        # legacy "last user message" preview.
        has_cjk = bool(re.search(r"[\u3400-\u9fff]", s))
        if has_cjk:
            max_chars = min(limit, 18)
            return s if len(s) <= max_chars else s[:max_chars].rstrip() + "…"

        words = s.split()
        if len(words) > 7:
            s = " ".join(words[:7])
        return cls._clip_text(s, limit)

    @classmethod
    def _is_fallback_title(cls, session_id: str, title: str) -> bool:
        title = (title or "").strip()
        return not title or title == cls._fallback_title(session_id)

    @staticmethod
    def _session_kind(session_id: str) -> str:
        if session_id.startswith("sched_"):
            return "scheduled"
        if session_id.startswith("auto_"):
            return "auto"
        if session_id.startswith("broadcast_"):
            return "broadcast"
        return "chat"

    @staticmethod
    def _fallback_title(session_id: str) -> str:
        if session_id.startswith("sched_"):
            return "Scheduled task"
        if session_id.startswith("broadcast_"):
            return "Broadcast run"
        if session_id.startswith("auto_"):
            return "Auto session"
        return f"Session {session_id[-8:]}"

    def _resolve_session_title_and_preview(self, item: dict, task_title_by_prefix: dict[str, str] | None = None) -> dict:
        d = dict(item)
        session_id = str(d.get("session_id") or "")
        kind = self._session_kind(session_id)
        first_user = str(d.get("first_user_message") or "")
        last_user = str(d.get("last_user_message") or "")
        title = ""
        if kind == "scheduled" and task_title_by_prefix:
            prefix = session_id[6:14] if len(session_id) >= 14 else ""
            if prefix:
                title = task_title_by_prefix.get(prefix, "")
        if not title:
            title = str(d.get("title") or d.get("stored_title") or "")
        first_topic = self._derive_session_title(first_user)
        last_topic = self._derive_session_title(last_user)
        title_source = str(d.get("title_source") or "")
        if title_source in _SESSION_TITLE_LOCKED_SOURCES:
            title = title or self._fallback_title(session_id)
        elif (
            title
            and first_topic
            and last_user
            and first_user.strip() != last_user.strip()
            and title in {last_topic, self._clip_text(last_user, 56)}
        ):
            title = first_topic
        if not title:
            title = first_topic or self._clip_text(first_user, 56) or self._fallback_title(session_id)

        d["title"] = title
        d["last_preview"] = self._clip_text(
            str(d.get("last_preview") or "") or last_user or str(d.get("content") or ""),
            72,
        )
        d["last_user_message"] = self._clip_text(last_user, 120)
        d["kind"] = kind
        d.pop("stored_title", None)
        return d

    @staticmethod
    def _decode_session_cursor(cursor: str | None) -> tuple[int, str] | None:
        raw = str(cursor or "").strip()
        if not raw or ":" not in raw:
            return None
        last_turn_raw, session_id = raw.split(":", 1)
        try:
            last_turn = int(last_turn_raw)
        except (TypeError, ValueError):
            return None
        session_id = session_id.strip()
        if not session_id:
            return None
        return last_turn, session_id

    @staticmethod
    def _encode_session_cursor(last_turn: int, session_id: str) -> str:
        return f"{int(last_turn)}:{str(session_id or '').strip()}"

    def list_sessions(
        self,
        limit: int = 200,
        include_scheduled: bool = True,
        max_idle_days: int = 0,
        workspace: str | None = None,
        offset: int = 0,
        cursor: str | None = None,
    ) -> list[dict]:
        now_ts = int(time.time())
        cutoff_ts = now_ts - max_idle_days * 86400 if max_idle_days > 0 else 0
        where = []
        params: list[object] = []
        if not include_scheduled:
            where.append("s.session_id NOT LIKE 'sched_%'")
        if cutoff_ts > 0:
            where.append("s.last_turn >= ?")
            params.append(cutoff_ts)
        if workspace is not None:
            where.append("COALESCE(s.workspace, '') = ?")
            params.append(workspace)
        cursor_parts = self._decode_session_cursor(cursor)
        if cursor_parts is not None:
            cursor_last_turn, cursor_session_id = cursor_parts
            where.append("(s.last_turn < ? OR (s.last_turn = ? AND s.session_id < ?))")
            params.extend([cursor_last_turn, cursor_last_turn, cursor_session_id])
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT s.session_id AS session_id, s.parent_session_id AS parent_session_id, "
            "s.source AS source, s.workspace AS workspace, "
            "COALESCE(NULLIF(s.kind, ''), 'chat') AS kind, "
            "s.compaction_count AS compaction_count, s.last_compacted_at AS last_compacted_at, "
            "s.turn_count AS turn_count, s.created AS started, s.last_turn AS last_turn, "
            "s.total_input_tokens AS total_input_tokens, s.total_output_tokens AS total_output_tokens, "
            "COALESCE(s.first_user_message, '') AS first_user_message, "
            "COALESCE(s.last_user_message, '') AS last_user_message, "
            "COALESCE(s.last_preview, '') AS last_preview, "
            "s.title AS stored_title, COALESCE(s.title_source, '') AS title_source "
            f"FROM sessions s {where_sql} "
            "ORDER BY s.last_turn DESC, s.session_id DESC LIMIT ?"
        )
        params.append(max(1, int(limit)))
        if cursor_parts is None and max(0, int(offset)) > 0:
            sql += " OFFSET ?"
            params.append(max(0, int(offset)))
        rows = self.conn.execute(sql, tuple(params)).fetchall()

        # scheduled session title lookup: sched_<taskIdPrefix>
        task_rows = self.conn.execute(
            "SELECT id, title FROM scheduled_tasks WHERE title IS NOT NULL AND title != ''"
        ).fetchall()
        task_title_by_prefix = {
            str(r["id"])[:8]: str(r["title"]).strip()
            for r in task_rows
            if r["id"] and r["title"]
        }

        out = []
        for r in rows:
            d = self._resolve_session_title_and_preview(dict(r), task_title_by_prefix=task_title_by_prefix)
            d.pop("title_source", None)
            out.append(d)
        return out

    def delete_session(self, session_id: str) -> bool:
        """Delete all turns for a session from the DB and its summary file."""
        self.conn.execute("DELETE FROM turns_fts WHERE session=?", (session_id,))
        self.conn.execute("DELETE FROM turns WHERE session=?", (session_id,))
        self.conn.execute("DELETE FROM session_lineage WHERE session_id=?", (session_id,))
        self.conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
        self.conn.commit()
        session_dir = self.sessions_dir / session_id
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
        return True

    def save_session_summary(self, session_id: str, summary: str) -> None:
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "summary.md").write_text(summary, encoding="utf-8")

    def load_session_summary(self, session_id: str) -> str | None:
        p = self.sessions_dir / session_id / "summary.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def save_session_working_state(self, session_id: str, state_text: str) -> None:
        """Persist a compact working-state checkpoint for a session."""
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "working_state.md").write_text(state_text, encoding="utf-8")

    def load_session_working_state(self, session_id: str) -> str | None:
        """Load the compact working-state checkpoint for a session."""
        p = self.sessions_dir / session_id / "working_state.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def save_global_working_state(self, text: str) -> None:
        """Persist cross-session goals/project context (survives all session boundaries)."""
        (self.data_dir / "global_working_state.md").write_text(text, encoding="utf-8")

    def load_global_working_state(self) -> str | None:
        """Load cross-session goals/project context, or None if not set."""
        p = self.data_dir / "global_working_state.md"
        try:
            return p.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def record_reflection(
        self,
        *,
        session_id: str,
        task_fingerprint: str,
        success: bool,
        outcome: str,
        failure_mode: str = "",
        lesson: str = "",
        strategy_hint: str = "",
        skill_name: str = "",
        source_turn_count: int = 0,
        source_message_id: str = "",
    ) -> str:
        reflection_id = make_id("refl-")
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO reflections "
            "(reflection_id, session_id, task_fingerprint, success, outcome, failure_mode, lesson, strategy_hint, skill_name, source_turn_count, source_message_id, created) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                reflection_id,
                session_id,
                task_fingerprint or "general_assistance",
                1 if success else 0,
                outcome or "",
                failure_mode or "",
                lesson or "",
                strategy_hint or "",
                skill_name or "",
                max(0, int(source_turn_count)),
                source_message_id or "",
                now,
            ),
        )
        self.conn.commit()
        return reflection_id

    def delete_reflections_by_source_message(self, source_message_id: str) -> int:
        mid = str(source_message_id or "").strip()
        if not mid:
            return 0
        cur = self.conn.execute(
            "DELETE FROM reflections WHERE source_message_id=?",
            (mid,),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def list_reflections(
        self,
        *,
        task_fingerprint: str = "",
        limit: int = 20,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if task_fingerprint:
            clauses.append("task_fingerprint=?")
            params.append(task_fingerprint)
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM reflections {where_sql} ORDER BY created DESC LIMIT ?",
            (*params, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_brief(self, session_id: str) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        row = self.conn.execute(
            "SELECT session_id, title, kind, workspace, last_turn, turn_count "
            "FROM sessions WHERE session_id=?",
            (sid,),
        ).fetchone()
        if row is None:
            return {"session_id": sid, "title": self._fallback_title(sid)}
        out = dict(row)
        if not out.get("title"):
            out["title"] = self._fallback_title(sid)
        return out

    def record_skill_outcome(
        self,
        *,
        skill_name: str,
        session_id: str,
        task_fingerprint: str = "",
        success: bool,
        note: str = "",
        quality_score: float = 1.0,
    ) -> str:
        outcome_id = make_id("sko-")
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO skill_outcomes "
            "(outcome_id, skill_name, session_id, task_fingerprint, success, note, quality_score, created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                outcome_id,
                skill_name,
                session_id,
                task_fingerprint or "",
                1 if success else 0,
                note or "",
                max(0.0, min(1.0, float(quality_score))),
                now,
            ),
        )
        self.conn.commit()
        return outcome_id

    def list_skill_outcomes(self, skill_name: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM skill_outcomes WHERE skill_name=? ORDER BY created DESC LIMIT ?",
            (skill_name, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_skill_outcomes(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM skill_outcomes ORDER BY created DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Scheduled tasks
    # ------------------------------------------------------------------

    def add_scheduled_task(
        self, cron: str, prompt: str, agent: str = "",
        run_once: bool = False, title: str = "",
    ) -> str:
        task_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO scheduled_tasks (id, cron, prompt, agent, created, run_once, title) "
            "VALUES (?,?,?,?,?,?,?)",
            (task_id, cron, prompt, agent, datetime.now().isoformat(),
             1 if run_once else 0, title),
        )
        self.conn.commit()
        return task_id

    def list_scheduled_tasks(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM scheduled_tasks ORDER BY created"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_active_scheduled_tasks(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled=1 ORDER BY created"
        ).fetchall()
        return [dict(r) for r in rows]

    def cancel_scheduled_task(self, task_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE scheduled_tasks SET enabled=0 WHERE id=?", (task_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def toggle_scheduled_task(self, task_id: str, enabled: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE scheduled_tasks SET enabled=? WHERE id=?",
            (1 if enabled else 0, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_scheduled_task(self, task_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_due_scheduled_tasks(self, now: datetime) -> list[dict]:
        from hushclaw.scheduler import _cron_matches
        return [t for t in self.list_active_scheduled_tasks() if _cron_matches(t["cron"], now)]

    def update_scheduled_task_last_run(self, task_id: str, ts: datetime) -> None:
        self.conn.execute(
            "UPDATE scheduled_tasks SET last_run=? WHERE id=?",
            (ts.isoformat(), task_id),
        )
        self.conn.commit()

    def disable_run_once_task(self, task_id: str) -> None:
        self.conn.execute(
            "UPDATE scheduled_tasks SET enabled=0 WHERE id=?", (task_id,)
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Todos
    # ------------------------------------------------------------------

    def add_todo(
        self,
        title: str,
        notes: str = "",
        priority: int = 0,
        due_at: int | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        import time as _time
        todo_id = "td-" + make_id()
        now = int(_time.time())
        tags_json = json.dumps(tags or [])
        self.conn.execute(
            "INSERT INTO todos (todo_id, title, notes, status, priority, due_at, tags, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (todo_id, title, notes, "pending", priority, due_at, tags_json, now, now),
        )
        self.conn.commit()
        return self.get_todo(todo_id)

    def get_todo(self, todo_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM todos WHERE todo_id=?", (todo_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        return d

    def list_todos(
        self,
        status: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict] | tuple[list[dict], bool]:
        fetch_limit = max(1, int(limit)) + 1 if limit is not None else None
        sql_limit = " LIMIT ? OFFSET ?" if fetch_limit is not None else ""
        if status:
            params: list[object] = [status]
            if fetch_limit is not None:
                params.extend([fetch_limit, max(0, int(offset))])
            rows = self.conn.execute(
                "SELECT * FROM todos WHERE status=? ORDER BY priority DESC, created ASC" + sql_limit,
                params,
            ).fetchall()
        else:
            params = []
            if fetch_limit is not None:
                params.extend([fetch_limit, max(0, int(offset))])
            rows = self.conn.execute(
                "SELECT * FROM todos ORDER BY priority DESC, created ASC" + sql_limit,
                params,
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            result.append(d)
        if limit is not None:
            has_more = len(result) > int(limit)
            if has_more:
                result = result[: int(limit)]
            return result, has_more
        return result

    def update_todo(self, todo_id: str, **fields) -> dict | None:
        import time as _time
        allowed = {"title", "notes", "status", "priority", "due_at", "tags"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_todo(todo_id)
        if "tags" in updates and isinstance(updates["tags"], list):
            updates["tags"] = json.dumps(updates["tags"])
        updates["updated"] = int(_time.time())
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [todo_id]
        self.conn.execute(
            f"UPDATE todos SET {set_clause} WHERE todo_id=?", values
        )
        self.conn.commit()
        return self.get_todo(todo_id)

    def delete_todo(self, todo_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM todos WHERE todo_id=?", (todo_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Lightweight TaskRun worker foundation
    # ------------------------------------------------------------------

    def create_task(
        self,
        title: str,
        spec: str = "",
        *,
        parent_task_id: str = "",
        dependencies: list[str] | None = None,
        workspace: str = "",
        model_override: str = "",
        metadata: dict | None = None,
        acceptance_criteria: list[str] | None = None,
        proof_required: str = "response",
        status: str = TASK_STATUS_QUEUED,
    ) -> dict:
        return self._task_runs.create_task(
            title,
            spec,
            parent_task_id=parent_task_id,
            dependencies=dependencies,
            workspace=workspace,
            model_override=model_override,
            metadata=metadata,
            acceptance_criteria=acceptance_criteria,
            proof_required=proof_required,
            status=status,
        )

    def get_task(self, task_id: str) -> dict | None:
        return self._task_runs.get_task(task_id)

    def list_tasks(self, status: str | None = None, limit: int = 100) -> list[dict]:
        return self._task_runs.list_tasks(status=status, limit=limit)

    def retry_task(self, task_id: str) -> dict | None:
        return self._task_runs.retry_task(task_id)

    def claim_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        session_id: str = "",
        ttl_seconds: int = 900,
    ) -> dict | None:
        return self._task_runs.claim(
            task_id,
            worker_id=worker_id,
            session_id=session_id,
            ttl_seconds=ttl_seconds,
        )

    def get_task_run(self, run_id: str) -> dict | None:
        return self._task_runs.get_run(run_id)

    def list_task_runs(self, task_id: str = "", status: str = "", limit: int = 50) -> list[dict]:
        return self._task_runs.list_runs(task_id=task_id, status=status, limit=limit)

    def complete_task_run(
        self,
        run_id: str,
        result: str = "",
        *,
        evidence: list[dict] | None = None,
        lease_token: str = "",
    ) -> bool:
        return self._task_runs.complete(
            run_id, result=result, evidence=evidence, lease_token=lease_token
        )

    def fail_task_run(
        self,
        run_id: str,
        error: str,
        error_fingerprint: str = "",
        *,
        evidence: list[dict] | None = None,
        lease_token: str = "",
    ) -> bool:
        return self._task_runs.fail(
            run_id,
            error,
            error_fingerprint=error_fingerprint,
            evidence=evidence,
            lease_token=lease_token,
        )

    def heartbeat_task_run(self, run_id: str, lease_token: str, ttl_seconds: int = 900) -> bool:
        return self._task_runs.heartbeat(run_id, lease_token, ttl_seconds=ttl_seconds)

    def mark_stale_task_runs(self, now: int | None = None) -> int:
        return self._task_runs.mark_stale(now=now)

    def task_reliability_summary(self) -> dict[str, int]:
        return self._task_runs.summary()

    @staticmethod
    def _fingerprint_error(error: str) -> str:
        return TaskRunStore.fingerprint_error(error)

    # ------------------------------------------------------------- app inbox

    def upsert_app_inbox_event(
        self,
        *,
        connector_id: str,
        event_type: str,
        external_id: str = "",
        title: str = "",
        body: str = "",
        source_url: str = "",
        payload: dict | None = None,
        status: str = "unread",
    ) -> dict:
        connector_id = str(connector_id or "").strip()
        event_type = str(event_type or "").strip()
        if not connector_id:
            raise ValueError("connector_id is required")
        if not event_type:
            raise ValueError("event_type is required")
        external_id = str(external_id or "").strip()
        status = str(status or "unread").strip() or "unread"
        now = int(time.time())
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        existing = None
        if external_id:
            existing = self.conn.execute(
                """
                SELECT event_id, created
                FROM app_inbox_events
                WHERE connector_id=? AND event_type=? AND external_id=?
                """,
                (connector_id, event_type, external_id),
            ).fetchone()
        event_id = existing["event_id"] if existing else "app_evt-" + make_id()
        created = int(existing["created"]) if existing else now
        self.conn.execute(
            """
            INSERT INTO app_inbox_events
                (event_id, connector_id, event_type, external_id, title, body,
                 source_url, payload_json, status, created, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                title=excluded.title,
                body=excluded.body,
                source_url=excluded.source_url,
                payload_json=excluded.payload_json,
                status=CASE
                    WHEN app_inbox_events.status IN ('archived', 'published', 'discarded')
                    THEN app_inbox_events.status
                    ELSE excluded.status
                END,
                updated=excluded.updated
            """,
            (
                event_id,
                connector_id,
                event_type,
                external_id,
                str(title or "")[:500],
                str(body or ""),
                str(source_url or ""),
                payload_json,
                status,
                created,
                now,
            ),
        )
        self.conn.commit()
        return self.get_app_inbox_event(event_id) or {}

    @staticmethod
    def _decode_app_inbox_payload(raw: str) -> dict:
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def get_app_inbox_event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM app_inbox_events WHERE event_id=?",
            (str(event_id or "").strip(),),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = self._decode_app_inbox_payload(item.pop("payload_json", "{}"))
        return item

    def list_app_inbox_events(
        self,
        connector_id: str = "",
        status: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        where: list[str] = []
        args: list[object] = []
        if connector_id:
            where.append("connector_id=?")
            args.append(str(connector_id))
        if status:
            where.append("status=?")
            args.append(str(status))
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        rows = self.conn.execute(
            f"SELECT * FROM app_inbox_events {where_sql} ORDER BY updated DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        items: list[dict] = []
        for row in rows:
            item = dict(row)
            item["payload"] = self._decode_app_inbox_payload(item.pop("payload_json", "{}"))
            items.append(item)
        return items

    def patch_app_inbox_event(
        self,
        event_id: str,
        *,
        status: str | None = None,
        title: str | None = None,
        body: str | None = None,
        source_url: str | None = None,
        payload_patch: dict | None = None,
    ) -> dict | None:
        event_id = str(event_id or "").strip()
        if not event_id:
            return None
        existing = self.get_app_inbox_event(event_id)
        if existing is None:
            return None
        payload = dict(existing.get("payload") or {})
        if isinstance(payload_patch, dict):
            payload.update(payload_patch)
        next_status = str(status or existing.get("status") or "").strip() or str(existing.get("status") or "unread")
        updates = {
            "title": str(existing.get("title") or "") if title is None else str(title or "")[:500],
            "body": str(existing.get("body") or "") if body is None else str(body or ""),
            "source_url": str(existing.get("source_url") or "") if source_url is None else str(source_url or ""),
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "status": next_status,
            "updated": int(time.time()),
            "event_id": event_id,
        }
        self.conn.execute(
            """
            UPDATE app_inbox_events
            SET title=:title,
                body=:body,
                source_url=:source_url,
                payload_json=:payload_json,
                status=:status,
                updated=:updated
            WHERE event_id=:event_id
            """,
            updates,
        )
        self.conn.commit()
        return self.get_app_inbox_event(event_id)

    def claim_app_inbox_event(
        self,
        event_id: str,
        *,
        from_statuses: list[str] | tuple[str, ...] | set[str],
        to_status: str = "pending",
        payload_patch: dict | None = None,
    ) -> dict | None:
        event_id = str(event_id or "").strip()
        allowed_from = [str(item or "").strip() for item in from_statuses if str(item or "").strip()]
        if not event_id or not allowed_from:
            return None
        row = self.conn.execute(
            "SELECT payload_json FROM app_inbox_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        payload = self._decode_app_inbox_payload(row["payload_json"])
        if isinstance(payload_patch, dict):
            payload.update(payload_patch)
        placeholders = ",".join("?" for _ in allowed_from)
        params: list[object] = [
            to_status,
            json.dumps(payload, ensure_ascii=False),
            int(time.time()),
            event_id,
            *allowed_from,
        ]
        cur = self.conn.execute(
            f"""
            UPDATE app_inbox_events
            SET status=?, payload_json=?, updated=?
            WHERE event_id=? AND status IN ({placeholders})
            """,
            params,
        )
        self.conn.commit()
        if cur.rowcount <= 0:
            return None
        return self.get_app_inbox_event(event_id)

    def update_app_inbox_event_status(self, event_id: str, status: str) -> dict | None:
        event_id = str(event_id or "").strip()
        status = str(status or "").strip()
        if not event_id or not status:
            return None
        allowed = {
            "unread",
            "read",
            "archived",
            "pending",
            "published",
            "discarded",
            "classified",
            "replied",
            "ignored",
            "failed",
            "auto_replied",
        }
        if status not in allowed:
            return None
        self.conn.execute(
            "UPDATE app_inbox_events SET status=?, updated=? WHERE event_id=?",
            (status, int(time.time()), event_id),
        )
        self.conn.commit()
        return self.get_app_inbox_event(event_id)

    # ------------------------------------------------------------------ calendar

    def add_calendar_event(
        self,
        title: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        color: str = "indigo",
        all_day: bool = False,
        attendees: list[str] | None = None,
        source: str = "local",
    ) -> dict:
        import time as _time
        event_id = "evt-" + make_id()
        now = int(_time.time())
        attendees_json = json.dumps(attendees or [])
        self.conn.execute(
            "INSERT INTO calendar_events "
            "(event_id, title, description, location, start_time, end_time, all_day, color, attendees, source, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, title, description, location, start_time, end_time, int(all_day), color, attendees_json, source, now, now),
        )
        self.conn.commit()
        return self.get_calendar_event(event_id)

    def upsert_caldav_event(
        self,
        event_id: str,
        title: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        all_day: bool = False,
        remote_uid: str = "",
        remote_href: str = "",
        remote_etag: str = "",
        recurrence_id: str = "",
        remote_calendar: str = "",
    ) -> int:
        """Insert or update a CalDAV-sourced event. Never overwrites source='local' rows.

        Returns 1 if the row was inserted or updated, 0 if skipped
        (e.g. a local event already owns that event_id).
        """
        import time as _time
        now = int(_time.time())
        cur = self.conn.execute(
            """
            INSERT INTO calendar_events
                (event_id, title, description, location, start_time, end_time,
                 all_day, color, attendees, source, remote_uid, remote_href,
                 remote_etag, recurrence_id, remote_calendar, last_seen_at,
                 created, updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                location=excluded.location,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                all_day=excluded.all_day,
                source='caldav',
                remote_uid=excluded.remote_uid,
                remote_href=excluded.remote_href,
                remote_etag=excluded.remote_etag,
                recurrence_id=excluded.recurrence_id,
                remote_calendar=excluded.remote_calendar,
                last_seen_at=excluded.last_seen_at,
                updated=excluded.updated
            WHERE source='caldav'
            """,
            (
                event_id,
                title,
                description,
                location,
                start_time,
                end_time,
                int(all_day),
                "indigo",
                "[]",
                "caldav",
                remote_uid,
                remote_href,
                remote_etag,
                recurrence_id,
                remote_calendar,
                now,
                now,
                now,
            ),
        )
        self.conn.commit()
        return cur.rowcount  # 1 = inserted or updated; 0 = skipped (source='local')

    def clear_caldav_events(self) -> int:
        """Delete ALL source='caldav' events. Used before a full re-sync."""
        cur = self.conn.execute("DELETE FROM calendar_events WHERE source='caldav'")
        self.conn.commit()
        return cur.rowcount

    def prune_stale_caldav_events(self, kept_ids: set) -> int:
        """Delete source='caldav' rows whose event_id is not in kept_ids.

        Called after a full CalDAV pull to remove events deleted on the server.
        Returns the number of rows deleted.
        """
        if not kept_ids:
            # Safety: if the pull returned nothing, don't wipe everything.
            return 0
        placeholders = ",".join("?" * len(kept_ids))
        cur = self.conn.execute(
            f"DELETE FROM calendar_events WHERE source='caldav' AND event_id NOT IN ({placeholders})",
            list(kept_ids),
        )
        self.conn.commit()
        return cur.rowcount

    def get_caldav_event_ids_for_resource(
        self,
        *,
        remote_href: str = "",
        remote_uid: str = "",
        remote_etag: str = "",
        remote_calendar: str = "",
    ) -> list[str]:
        """Return existing CalDAV event IDs for one remote resource.

        Used to short-circuit unchanged non-recurring resources when the
        remote ETag matches what we already have locally.
        """
        if remote_href:
            where = ["source='caldav'", "remote_href=?"]
            params: list[str] = [remote_href]
            if remote_etag:
                where.append("remote_etag=?")
                params.append(remote_etag)
            rows = self.conn.execute(
                f"SELECT event_id FROM calendar_events WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
            return [str(r[0]) for r in rows]
        if remote_uid:
            where = ["source='caldav'", "remote_uid=?"]
            params = [remote_uid]
            if remote_calendar:
                where.append("remote_calendar=?")
                params.append(remote_calendar)
            if remote_etag:
                where.append("remote_etag=?")
                params.append(remote_etag)
            rows = self.conn.execute(
                f"SELECT event_id FROM calendar_events WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
            return [str(r[0]) for r in rows]
        return []

    def get_caldav_event_ids_for_calendar(self, remote_calendar: str) -> list[str]:
        if not remote_calendar:
            return []
        rows = self.conn.execute(
            "SELECT event_id FROM calendar_events WHERE source='caldav' AND remote_calendar=?",
            (remote_calendar,),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def delete_caldav_events_by_resource(
        self,
        *,
        remote_href: str = "",
        remote_uid: str = "",
        remote_calendar: str = "",
    ) -> int:
        if remote_href:
            cur = self.conn.execute(
                "DELETE FROM calendar_events WHERE source='caldav' AND remote_href=?",
                (remote_href,),
            )
            self.conn.commit()
            return cur.rowcount
        if remote_uid:
            where = ["source='caldav'", "remote_uid=?"]
            params: list[str] = [remote_uid]
            if remote_calendar:
                where.append("remote_calendar=?")
                params.append(remote_calendar)
            cur = self.conn.execute(
                f"DELETE FROM calendar_events WHERE {' AND '.join(where)}",
                params,
            )
            self.conn.commit()
            return cur.rowcount
        return 0

    def touch_caldav_events_seen(self, event_ids: list[str] | set[str]) -> int:
        """Refresh last_seen_at for existing CalDAV rows reused via ETag short-circuit."""
        ids = [str(eid) for eid in event_ids if eid]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        now = int(time.time())
        cur = self.conn.execute(
            f"UPDATE calendar_events SET last_seen_at=? WHERE source='caldav' AND event_id IN ({placeholders})",
            [now] + ids,
        )
        self.conn.commit()
        return cur.rowcount

    def get_caldav_collection_state(self, collection_key: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT collection_key, last_ctag, last_sync_token, last_scan_at, last_result_count, updated
            FROM caldav_collection_state
            WHERE collection_key=?
            """,
            (collection_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_caldav_collection_state(
        self,
        collection_key: str,
        *,
        last_ctag: str,
        last_sync_token: str,
        last_scan_at: int,
        last_result_count: int,
    ) -> dict:
        updated = int(time.time())
        self.conn.execute(
            """
            INSERT INTO caldav_collection_state
                (collection_key, last_ctag, last_sync_token, last_scan_at, last_result_count, updated)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(collection_key) DO UPDATE SET
                last_ctag=excluded.last_ctag,
                last_sync_token=excluded.last_sync_token,
                last_scan_at=excluded.last_scan_at,
                last_result_count=excluded.last_result_count,
                updated=excluded.updated
            """,
            (
                str(collection_key or "")[:1000],
                str(last_ctag or "")[:1000],
                str(last_sync_token or "")[:4000],
                max(0, int(last_scan_at)),
                max(0, int(last_result_count)),
                updated,
            ),
        )
        self.conn.commit()
        return self.get_caldav_collection_state(collection_key) or {}

    def get_caldav_sync_state(self, sync_key: str = "default") -> dict | None:
        row = self.conn.execute(
            """
            SELECT sync_key, last_attempt, last_success, last_failure,
                   failure_count, last_error, last_result_count, updated
            FROM caldav_sync_state
            WHERE sync_key=?
            """,
            (sync_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_caldav_sync_state(
        self,
        sync_key: str,
        *,
        last_attempt: int,
        last_success: int,
        last_failure: int,
        failure_count: int,
        last_error: str,
        last_result_count: int,
    ) -> dict:
        updated = int(time.time())
        self.conn.execute(
            """
            INSERT INTO caldav_sync_state
                (sync_key, last_attempt, last_success, last_failure,
                 failure_count, last_error, last_result_count, updated)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(sync_key) DO UPDATE SET
                last_attempt=excluded.last_attempt,
                last_success=excluded.last_success,
                last_failure=excluded.last_failure,
                failure_count=excluded.failure_count,
                last_error=excluded.last_error,
                last_result_count=excluded.last_result_count,
                updated=excluded.updated
            """,
            (
                sync_key,
                max(0, int(last_attempt)),
                max(0, int(last_success)),
                max(0, int(last_failure)),
                max(0, int(failure_count)),
                str(last_error or "")[:1000],
                max(0, int(last_result_count)),
                updated,
            ),
        )
        self.conn.commit()
        return self.get_caldav_sync_state(sync_key) or {}

    def get_calendar_event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM calendar_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["attendees"] = json.loads(d.get("attendees") or "[]")
        d["all_day"] = bool(d.get("all_day", 0))
        return d

    def list_calendar_events(
        self,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> list[dict]:
        # Interval overlap: event overlaps [from, to) when start < to AND end > from.
        # This correctly captures ongoing events that started before the query window.
        if from_time and to_time:
            rows = self.conn.execute(
                "SELECT * FROM calendar_events WHERE start_time < ? AND end_time > ? "
                "ORDER BY start_time ASC",
                (to_time, from_time),
            ).fetchall()
        elif from_time:
            rows = self.conn.execute(
                "SELECT * FROM calendar_events WHERE end_time > ? ORDER BY start_time ASC",
                (from_time,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM calendar_events ORDER BY start_time ASC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["attendees"] = json.loads(d.get("attendees") or "[]")
            d["all_day"] = bool(d.get("all_day", 0))
            result.append(d)
        return result

    def update_calendar_event(self, event_id: str, **fields) -> dict | None:
        import time as _time
        allowed = {"title", "description", "location", "start_time", "end_time", "all_day", "color", "attendees"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_calendar_event(event_id)
        if "attendees" in updates and isinstance(updates["attendees"], list):
            updates["attendees"] = json.dumps(updates["attendees"])
        if "all_day" in updates:
            updates["all_day"] = int(bool(updates["all_day"]))
        updates["updated"] = int(_time.time())
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [event_id]
        self.conn.execute(
            f"UPDATE calendar_events SET {set_clause} WHERE event_id=?", values
        )
        self.conn.commit()
        return self.get_calendar_event(event_id)

    def delete_calendar_event(self, event_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM calendar_events WHERE event_id=?", (event_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()
