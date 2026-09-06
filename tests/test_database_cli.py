from __future__ import annotations

import os
from pathlib import Path

from hushclaw.cli.database import database_format, inspect_database
from hushclaw.memory.db import APPLICATION_ID, SCHEMA_VERSION
from hushclaw.memory.store import MemoryStore


def test_database_status_distinguishes_plaintext_and_reports_health(tmp_path: Path):
    store = MemoryStore(tmp_path)
    store.close()

    status = inspect_database(tmp_path)
    assert database_format(tmp_path / "memory.db") == "sqlite-plaintext"
    assert status["schema_version"] == SCHEMA_VERSION
    assert status["application_id"] == APPLICATION_ID
    assert status["application_id_valid"] is True
    assert status["migration_ledger"] is True
    assert status["quick_check"] == "ok"
    assert status["foreign_key_violations"] == 0
    assert status["journal_mode"] == "wal"
    assert status["reclaimable_bytes"] >= 0
    if os.name == "posix":
        assert status["data_dir_private"] is True
        assert status["db_private"] is True


def test_database_format_does_not_claim_unknown_bytes_are_encrypted(tmp_path: Path):
    path = tmp_path / "memory.db"
    path.write_bytes(b"not a sqlite database")
    assert database_format(path) == "sqlcipher-or-unknown"
