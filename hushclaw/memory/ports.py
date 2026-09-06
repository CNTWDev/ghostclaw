"""MemoryPort boundary for Agent OS storage adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from hushclaw.memory.events import _conn_lock
from hushclaw.memory.fts import FTSSearch
from hushclaw.memory.kinds import RECALL_MEMORY_KINDS, SYSTEM_MEMORY_TAGS
from hushclaw.memory.sqlite_runtime import SQLiteReadConnections
from hushclaw.memory.store import _FTS_SHORTCUT_THRESHOLD
from hushclaw.memory.vectors import VectorStore

if TYPE_CHECKING:
    from hushclaw.runtime.principal import RuntimePrincipal


@dataclass(slots=True)
class MemoryRecord:
    content: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    scope: str = "global"
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryPort(ABC):
    @abstractmethod
    def remember(
        self,
        content: str,
        *,
        scope: str = "global",
        principal: RuntimePrincipal | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def recall(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> str: ...

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> list[dict]: ...

    @abstractmethod
    def update(
        self,
        note_id: str,
        content: str,
        *,
        principal: RuntimePrincipal | None = None,
        tags: list[str] | None = None,
    ) -> bool: ...

    @abstractmethod
    def delete(self, note_id: str, *, principal: RuntimePrincipal | None = None) -> bool: ...

    @abstractmethod
    def promote(
        self,
        note_id: str,
        target_scope: str,
        *,
        principal: RuntimePrincipal | None = None,
    ) -> bool: ...


class SQLiteMemoryPort(MemoryPort):
    """Adapter around the existing MemoryStore facade."""

    def __init__(self, store) -> None:
        self.store = store
        conn = getattr(store, "conn", None)
        self._lock = _conn_lock(conn) if conn is not None else None
        self._read_connections = getattr(store, "_read_connections", None)
        if self._read_connections is None and getattr(store, "data_dir", None) is not None:
            self._read_connections = SQLiteReadConnections(
                store.data_dir,
                database_encryption=getattr(store, "database_encryption", "auto"),
            )
            setattr(store, "_read_connections", self._read_connections)

    def _locked(self, fn):
        if self._lock is None:
            return fn()
        with self._lock:
            return fn()

    def _read_conn(self):
        return self._read_connections.connection() if self._read_connections is not None else None

    def _read_vector_store(self, conn) -> VectorStore:
        vec = getattr(self.store, "_vec", None)
        return VectorStore(
            conn,
            getattr(vec, "embed_provider", "local"),
            getattr(vec, "api_key", ""),
            getattr(vec, "embed_model", ""),
        )

    def remember(
        self,
        content: str,
        *,
        scope: str = "global",
        principal: RuntimePrincipal | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        from hushclaw.runtime.principal import current_principal  # lazy — avoids circular import through memory.__init__
        principal = principal or current_principal()
        metadata = metadata or {}
        tags = list(metadata.get("tags") or [])
        title = str(metadata.get("title") or "")
        note_type = str(metadata.get("note_type") or "fact")
        memory_kind = str(metadata.get("memory_kind") or "")
        return self._locked(
            lambda: self.store.remember(
                content,
                title=title,
                tags=tags,
                scope=scope,
                note_type=note_type,
                memory_kind=memory_kind,
            )
        )

    def recall(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> str:
        conn = self._read_conn()
        if conn is None:
            return self._locked(lambda: self.store.recall(query, limit=limit))
        results = self._read_search(query, limit=limit, scopes=scopes, include_kinds=RECALL_MEMORY_KINDS)
        if results:
            return "\n\n".join(
                f"[{r.get('title') or r.get('note_id')}]\n{str(r.get('body') or '')[:300]}"
                for r in results[:limit]
            )
        if scopes:
            rows = self._read_recent_notes_by_scopes(conn, scopes, limit=limit, include_kinds=RECALL_MEMORY_KINDS)
            if rows:
                return "\n\n".join(
                    f"[{i}] {r.get('title') or r.get('note_id')}\n{str(r.get('body') or '')[:300]}"
                    for i, r in enumerate(rows, 1)
                )
        return ""

    def search(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        principal: RuntimePrincipal | None = None,
        limit: int = 5,
    ) -> list[dict]:
        conn = self._read_conn()
        if conn is None:
            return self._locked(lambda: self.store.search(query, limit=limit))
        return self._read_search(query, limit=limit, scopes=scopes)

    def _read_search(
        self,
        query: str,
        *,
        limit: int = 5,
        scopes: list[str] | None = None,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        conn = self._read_conn()
        if conn is None:
            return self.store.search(query, limit=limit, include_kinds=include_kinds)
        visible_kinds = include_kinds
        fts = FTSSearch(conn)
        vec = self._read_vector_store(conn)
        blocked_tags = sorted(SYSTEM_MEMORY_TAGS)
        fts_results = fts.search(query, limit * 2, scopes=scopes, exclude_tags=blocked_tags)
        fts_max = max((r.get("score_fts", 0.0) for r in fts_results), default=0.0)
        if fts_results and fts_max >= _FTS_SHORTCUT_THRESHOLD:
            merged = [
                {
                    "note_id": r["note_id"],
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "tags": r.get("tags", []),
                    "score": self.store.fts_weight * r.get("score_fts", 0.0),
                }
                for r in fts_results
            ]
        else:
            fts_map = {r["note_id"]: r for r in fts_results}
            vec_results = {r["note_id"]: r for r in vec.search(query, limit * 2, scopes=scopes, exclude_tags=blocked_tags)}
            merged = []
            for note_id in set(fts_map) | set(vec_results):
                fts_score = fts_map.get(note_id, {}).get("score_fts", 0.0)
                vec_score = vec_results.get(note_id, {}).get("score_vec", 0.0)
                note = fts_map.get(note_id) or vec_results.get(note_id, {})
                merged.append({
                    "note_id": note_id,
                    "title": note.get("title", ""),
                    "body": note.get("body", ""),
                    "tags": note.get("tags", []),
                    "score": self.store.fts_weight * fts_score + self.store.vec_weight * vec_score,
                })

        meta = self._read_note_metadata(conn, [r["note_id"] for r in merged])
        filtered = []
        for item in merged:
            _rc, note_type, memory_kind = meta.get(item["note_id"], (0, "fact", "project_knowledge"))
            if visible_kinds and memory_kind not in visible_kinds:
                continue
            item["note_type"] = note_type
            item["memory_kind"] = memory_kind
            filtered.append(item)
        filtered.sort(key=lambda item: item["score"], reverse=True)
        return filtered[:limit]

    @staticmethod
    def _read_note_metadata(conn, note_ids: list[str]) -> dict[str, tuple[int, str, str]]:
        if not note_ids:
            return {}
        placeholders = ",".join("?" * len(note_ids))
        rows = conn.execute(
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

    @staticmethod
    def _read_recent_notes_by_scopes(
        conn,
        scopes: list[str],
        *,
        limit: int,
        include_kinds: set[str] | None = None,
    ) -> list[dict]:
        scope_ph = ",".join("?" * len(scopes))
        clauses = [f"n.scope IN ({scope_ph})"]
        params: list[object] = list(scopes)
        if include_kinds:
            kind_ph = ",".join("?" * len(include_kinds))
            clauses.append(f"n.memory_kind IN ({kind_ph})")
            params.extend(sorted(include_kinds))
        rows = conn.execute(
            f"SELECT n.note_id, n.title, n.tags, n.scope, n.note_type, n.memory_kind, b.body FROM notes n "
            f"LEFT JOIN note_bodies b USING(note_id) "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY n.modified DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        import json
        return [{**dict(row), "tags": json.loads(row["tags"] or "[]")} for row in rows]

    def update(
        self,
        note_id: str,
        content: str,
        *,
        principal: RuntimePrincipal | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        return self._locked(lambda: self.store.update_note(note_id, content, tags))

    def delete(self, note_id: str, *, principal: RuntimePrincipal | None = None) -> bool:
        return self._locked(lambda: self.store.delete_note(note_id))

    def promote(
        self,
        note_id: str,
        target_scope: str,
        *,
        principal: RuntimePrincipal | None = None,
    ) -> bool:
        def _do() -> bool:
            note = self.store.get_note(note_id)
            if not note:
                return False
            content = str(note.get("body") or note.get("content") or "")
            title = str(note.get("title") or "")
            tags = list(note.get("tags") or [])
            self.store.remember(content, title=title, tags=tags, scope=target_scope)
            return True

        return self._locked(_do)
