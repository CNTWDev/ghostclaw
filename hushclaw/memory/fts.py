"""FTS5 full-text search over notes."""
from __future__ import annotations

import json
import re
import sqlite3

from hushclaw.memory.encryption import OPERATIONAL_ERRORS

# Matches any CJK character (CJK Unified, Hiragana/Katakana, Hangul).
_CJK_CHAR = re.compile(r"[一-鿿぀-ヿ가-힯]")
_CJK_RUN  = re.compile(r"[一-鿿぀-ヿ가-힯]{2,}")
_ASCII_TERM = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+'’:-]*")


def _quote_fts_phrase(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _build_fts_query(query: str) -> str:
    """Build an FTS5 MATCH expression that works for both ASCII and CJK input.

    Trigram tokenizer (used by notes_fts) requires ≥ 3 consecutive characters
    per search term. For CJK-heavy queries, the raw sentence cannot be used
    verbatim because AND semantics would require every token to appear in a
    single document. Instead, extract 4-char sliding windows from each CJK run
    and join them with OR so any document containing a key phrase is returned.
    """
    raw = (query or "").strip()
    safe_terms = [_quote_fts_phrase(term) for term in _ASCII_TERM.findall(raw)]
    if len(_CJK_CHAR.findall(query)) < 3:
        return " ".join(safe_terms) if safe_terms else _quote_fts_phrase(raw)

    runs = _CJK_RUN.findall(query)
    if not runs:
        return " ".join(safe_terms) if safe_terms else _quote_fts_phrase(raw)

    ngrams: list[str] = []
    for run in runs:
        if len(run) >= 3:
            if len(run) <= 4:
                ngrams.append(run)
            else:
                for i in range(len(run) - 3):
                    ngrams.append(run[i : i + 4])  # 4-char windows

    # Deduplicate, cap to keep the MATCH expression manageable.
    seen: set[str] = set()
    unique: list[str] = []
    for ng in ngrams:
        if ng not in seen:
            seen.add(ng)
            unique.append(ng)
    unique = unique[:10]

    ascii_words = _ASCII_TERM.findall(query)[:3]
    parts = [_quote_fts_phrase(ng) for ng in unique] + [_quote_fts_phrase(w) for w in ascii_words]
    return " OR ".join(parts) if parts else (_quote_fts_phrase(raw) if raw else "")


class FTSSearch:
    """BM25 full-text search using SQLite FTS5."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def search(
        self,
        query: str,
        limit: int = 10,
        scopes: list[str] | None = None,
        exclude_tags: list[str] | None = None,
    ) -> list[dict]:
        """Return notes matching query, ranked by BM25 score."""
        if not query.strip():
            return []
        # Build a tokenizer-aware FTS5 MATCH expression.
        # For CJK input, extracts 4-char n-grams joined with OR.
        safe_q = _build_fts_query(query)

        extra_clause = ""
        extra_params: tuple = ()
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            extra_clause += f" AND n.scope IN ({placeholders})"
            extra_params += tuple(scopes)
        if exclude_tags:
            placeholders = ",".join("?" * len(exclude_tags))
            extra_clause += (
                f" AND NOT EXISTS ("
                f"SELECT 1 FROM json_each(n.tags) "
                f"WHERE json_each.value IN ({placeholders}))"
            )
            extra_params += tuple(exclude_tags)

        def _run(q: str) -> list:
            return self.conn.execute(
                f"""
                SELECT n.note_id, n.title, n.tags, n.created, n.modified,
                       b.body,
                       bm25(notes_fts) AS score
                FROM notes_fts
                JOIN notes n ON notes_fts.note_id = n.note_id
                JOIN note_bodies b ON b.note_id = n.note_id
                WHERE notes_fts MATCH ?{extra_clause}
                ORDER BY score
                LIMIT ?
                """,
                (q,) + extra_params + (limit,),
            ).fetchall()

        try:
            rows = _run(safe_q)
        except OPERATIONAL_ERRORS:
            # Query syntax error — try simple prefix match
            safe_q = " ".join(f'"{w}"' for w in query.split() if w)
            try:
                rows = _run(safe_q)
            except OPERATIONAL_ERRORS:
                return []

        return [
            {
                "note_id": r["note_id"],
                "title": r["title"],
                "tags": json.loads(r["tags"] or "[]"),
                "body": r["body"],
                "created": r["created"],
                "score_fts": abs(float(r["score"])),
            }
            for r in rows
        ]
