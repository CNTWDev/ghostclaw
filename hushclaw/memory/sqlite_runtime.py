"""SQLite connection helpers for local memory storage."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


def configure_sqlite_connection(
    conn: sqlite3.Connection,
    *,
    readonly: bool = False,
    row_factory=None,
) -> sqlite3.Connection:
    conn.row_factory = row_factory or sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA cache_size = -32768")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 134217728")
    if readonly:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA synchronous = NORMAL")
        # Overwrite deleted cells when practical. FAST avoids extra I/O while
        # still preventing most deleted content from lingering in DB pages.
        conn.execute("PRAGMA secure_delete = FAST")
        conn.execute("PRAGMA journal_size_limit = 67108864")
    return conn


class SQLiteReadConnections:
    """Thread-local readonly connections for parallel recall/search paths."""

    def __init__(self, data_dir: Path, *, database_encryption: str = "auto") -> None:
        self.data_dir = Path(data_dir)
        self.db_path = Path(data_dir) / "memory.db"
        self.database_encryption = database_encryption
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            from hushclaw.memory.encryption import connect_database, get_sqlcipher_driver

            conn, encrypted, _key = connect_database(
                self.data_dir,
                mode=self.database_encryption,
                readonly=True,
                check_same_thread=False,
                isolation_level=None,
            )
            row_factory = get_sqlcipher_driver().Row if encrypted else sqlite3.Row
            configure_sqlite_connection(conn, readonly=True, row_factory=row_factory)
            self._local.conn = conn
        return conn
