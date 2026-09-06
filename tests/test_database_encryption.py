from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("sqlcipher3")

from hushclaw.cli.backup import create_backup_archive, restore_backup_archive
from hushclaw.cli.database import _replace_with_encrypted, inspect_database
from hushclaw.memory.encryption import (
    INTEGRITY_ERRORS,
    OPERATIONAL_ERRORS,
    DATABASE_KEY_ENV,
    DPAPI_KEY_NAME,
    FILE_KEY_NAME,
    database_file_format,
)
from hushclaw.memory.store import MemoryStore


TEST_KEY = "7a" * 32


def test_sqlcipher_exceptions_are_part_of_runtime_error_boundaries():
    from sqlcipher3 import dbapi2 as sqlcipher

    assert sqlcipher.IntegrityError in INTEGRITY_ERRORS
    assert sqlcipher.OperationalError in OPERATIONAL_ERRORS


def test_sqlcipher_roundtrip_preserves_schema_fts_and_runtime_reads(tmp_path, monkeypatch):
    monkeypatch.setenv(DATABASE_KEY_ENV, TEST_KEY)
    data_dir = tmp_path / "data"
    store = MemoryStore(data_dir)
    note_id = store.remember("encrypted searchable memory", title="secret")
    store.close()

    _replace_with_encrypted(data_dir / "memory.db", TEST_KEY)
    assert database_file_format(data_dir / "memory.db") == "sqlcipher-or-unknown"
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(data_dir / "memory.db").execute("SELECT count(*) FROM notes").fetchone()

    encrypted = MemoryStore(data_dir, database_encryption="sqlcipher")
    try:
        assert encrypted.get_note(note_id)["body"] == "encrypted searchable memory"
        assert "encrypted searchable" in encrypted.recall("searchable")
    finally:
        encrypted.close()

    status = inspect_database(data_dir)
    assert status["format"] == "sqlcipher"
    assert status["quick_check"] == "ok"


def test_encrypted_backup_stays_encrypted_and_restores_with_recovery_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(DATABASE_KEY_ENV, TEST_KEY)
    source = tmp_path / "source"
    store = MemoryStore(source)
    store.remember("portable encrypted backup", title="backup")
    store.close()
    _replace_with_encrypted(source / "memory.db", TEST_KEY)
    (source / FILE_KEY_NAME).write_text(TEST_KEY, encoding="ascii")
    (source / DPAPI_KEY_NAME).write_text("device-bound-secret", encoding="ascii")

    config = tmp_path / "hushclaw.toml"
    config.write_text(
        f'[memory]\ndata_dir = "{source}"\ndatabase_encryption = "sqlcipher"\n',
        encoding="utf-8",
    )
    archive = tmp_path / "backup.zip"
    create_backup_archive(archive, config_file=config, data_dir=source)
    with zipfile.ZipFile(archive) as zf:
        assert zf.read("data/memory.db")[:16] != b"SQLite format 3\x00"
        assert "data/.database-key" not in zf.namelist()
        assert "data/.database-key.dpapi" not in zf.namelist()

    restored = tmp_path / "restored"
    restore_backup_archive(
        archive,
        config_file=tmp_path / "restored.toml",
        data_dir=restored,
        restore_config=False,
        database_key=TEST_KEY,
    )
    opened = MemoryStore(restored, database_encryption="sqlcipher")
    try:
        assert "portable encrypted backup" in opened.recall("portable")
    finally:
        opened.close()
