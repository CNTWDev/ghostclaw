"""Database inspection and local filesystem hardening commands."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from hushclaw.core.storage_security import (
    create_private_file,
    ensure_private_file,
    harden_database_files,
    is_private_mode,
    posix_mode,
)
from hushclaw.memory.db import APPLICATION_ID, DB_NAME, SCHEMA_VERSION
from hushclaw.memory.encryption import (
    DATABASE_KEY_ENV,
    DatabaseEncryptionError,
    DatabaseKeyStore,
    connect_sqlcipher,
    database_file_format,
    get_sqlcipher_driver,
    key_status,
    validate_database_key,
)


database_format = database_file_format


def inspect_database(data_dir: Path, *, integrity: bool = True) -> dict:
    data_dir = Path(data_dir)
    db_path = data_dir / DB_NAME
    result = {
        "data_dir": str(data_dir),
        "db_path": str(db_path),
        "format": database_format(db_path),
        "data_dir_mode": posix_mode(data_dir),
        "db_mode": posix_mode(db_path),
        "data_dir_private": is_private_mode(data_dir, directory=True),
        "db_private": is_private_mode(db_path, directory=False),
        "schema_version": None,
        "supported_schema_version": SCHEMA_VERSION,
        "application_id": None,
        "application_id_valid": None,
        "migration_ledger": None,
        "quick_check": None,
        "foreign_key_violations": None,
        "page_size": None,
        "page_count": None,
        "freelist_count": None,
        "reclaimable_bytes": None,
        "journal_mode": None,
        "key_available": None,
        "key_source": "",
        "open_error": "",
    }
    if result["format"] not in {"sqlite-plaintext", "sqlcipher-or-unknown"}:
        return result

    if result["format"] == "sqlite-plaintext":
        uri = f"file:{db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        available, source = key_status(data_dir)
        result["key_available"] = available
        result["key_source"] = source
        if not available:
            return result
        key, _source = DatabaseKeyStore(data_dir).get()
        try:
            conn = connect_sqlcipher(db_path, key, readonly=True)
            result["format"] = "sqlcipher"
        except DatabaseEncryptionError as exc:
            result["open_error"] = str(exc)
            return result
    try:
        result["schema_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])
        result["application_id"] = int(conn.execute("PRAGMA application_id").fetchone()[0])
        result["application_id_valid"] = result["application_id"] == APPLICATION_ID
        ledger = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        result["migration_ledger"] = bool(ledger)
        result["page_size"] = int(conn.execute("PRAGMA page_size").fetchone()[0])
        result["page_count"] = int(conn.execute("PRAGMA page_count").fetchone()[0])
        result["freelist_count"] = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        result["reclaimable_bytes"] = result["page_size"] * result["freelist_count"]
        result["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        if integrity:
            row = conn.execute("PRAGMA quick_check").fetchone()
            result["quick_check"] = str(row[0]) if row else "no result"
            result["foreign_key_violations"] = len(
                conn.execute("PRAGMA foreign_key_check").fetchall()
            )
    finally:
        conn.close()
    return result


def _mode_text(mode: int | None) -> str:
    return "n/a" if mode is None else f"{mode:04o}"


def cmd_database_status(args) -> int:
    from hushclaw.config.loader import load_config

    data_dir = load_config().memory.data_dir
    status = inspect_database(data_dir)
    print(f"Database: {status['db_path']}")
    print(f"  format: {status['format']}")
    print(
        f"  permissions: dir={_mode_text(status['data_dir_mode'])} "
        f"db={_mode_text(status['db_mode'])}"
    )
    if status["format"] == "sqlite-plaintext":
        print("  encryption: off (owner-only permissions, not same-user process isolation)")
        print(
            f"  schema: {status['schema_version']}/{status['supported_schema_version']} "
            f"ledger={'ok' if status['migration_ledger'] else 'missing'}"
        )
        print(
            f"  integrity: {status['quick_check']} "
            f"foreign-key-violations={status['foreign_key_violations']}"
        )
        reclaimable_mb = (status["reclaimable_bytes"] or 0) / (1024 * 1024)
        print(f"  journal: {status['journal_mode']} reclaimable={reclaimable_mb:.1f} MiB")
    elif status["format"] == "sqlcipher":
        print(f"  encryption: SQLCipher (key source: {status['key_source']})")
        print(
            f"  schema: {status['schema_version']}/{status['supported_schema_version']} "
            f"ledger={'ok' if status['migration_ledger'] else 'missing'}"
        )
        print(
            f"  integrity: {status['quick_check']} "
            f"foreign-key-violations={status['foreign_key_violations']}"
        )
    elif status["format"] == "sqlcipher-or-unknown":
        detail = status["open_error"] or "database key or SQLCipher driver unavailable"
        print(f"  encryption: locked or unknown ({detail})")
    return 0


def cmd_database_harden(args) -> int:
    from hushclaw.config.loader import load_config

    data_dir = load_config().memory.data_dir
    harden_database_files(data_dir, DB_NAME)
    status = inspect_database(data_dir, integrity=False)
    print(f"Hardened database permissions: {status['db_path']}")
    print(
        f"  permissions: dir={_mode_text(status['data_dir_mode'])} "
        f"db={_mode_text(status['db_mode'])}"
    )
    return 0


def _sql_path(path: Path) -> str:
    return str(Path(path).resolve()).replace("'", "''")


def _export_database(source: Path, target: Path, *, source_key: str = "", target_key: str = "") -> None:
    """Export through SQLCipher so both plaintext and encrypted sides are supported."""
    driver = get_sqlcipher_driver()
    target = Path(target)
    if target.exists():
        target.unlink()
    create_private_file(target)
    conn = driver.connect(str(source), isolation_level=None)
    try:
        if source_key:
            conn.execute(f'''PRAGMA key = "x'{validate_database_key(source_key)}'"''')
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.execute("PRAGMA busy_timeout = 30000")
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0] or 0) != 0:
            raise DatabaseEncryptionError("database is busy; stop HushClaw before encryption")
        key_clause = (
            f'''"x'{validate_database_key(target_key)}'"''' if target_key else "''"
        )
        conn.execute(f"ATTACH DATABASE '{_sql_path(target)}' AS converted KEY {key_clause}")
        conn.execute("SELECT sqlcipher_export('converted')")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
        conn.execute(f"PRAGMA converted.user_version = {version}")
        conn.execute(f"PRAGMA converted.application_id = {app_id}")
        conn.execute("DETACH DATABASE converted")
    finally:
        conn.close()
    ensure_private_file(target)


def _verify_sqlcipher(path: Path, key: str) -> None:
    conn = connect_sqlcipher(path, key, readonly=True)
    try:
        check = conn.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise DatabaseEncryptionError(f"encrypted database integrity check failed: {check}")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DatabaseEncryptionError("encrypted database has foreign-key violations")
    finally:
        conn.close()


def _replace_with_encrypted(path: Path, key: str) -> None:
    target = path.with_name(f".{path.name}.sqlcipher.tmp")
    rollback = path.with_name(f".{path.name}.plaintext.rollback")
    _export_database(path, target, target_key=key)
    _verify_sqlcipher(target, key)
    if rollback.exists():
        rollback.unlink()
    os.replace(path, rollback)
    try:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
        os.replace(target, path)
        _verify_sqlcipher(path, key)
    except Exception:
        if path.exists():
            path.unlink()
        os.replace(rollback, path)
        raise
    else:
        rollback.unlink()
    ensure_private_file(path)


def _encrypt_migration_backups(data_dir: Path, key: str) -> tuple[int, int]:
    converted = skipped = 0
    backup_dir = Path(data_dir) / "backups" / "memory-db"
    if not backup_dir.exists():
        return converted, skipped
    for path in sorted(backup_dir.glob("*.db")):
        if database_file_format(path) == "sqlite-plaintext":
            _replace_with_encrypted(path, key)
            converted += 1
        else:
            skipped += 1
    return converted, skipped


def cmd_database_encrypt(args) -> int:
    from hushclaw.config.loader import get_config_dir, load_config
    from hushclaw.config.writer import set_config_value

    config = load_config()
    data_dir = Path(config.memory.data_dir)
    db_path = data_dir / DB_NAME
    fmt = database_file_format(db_path)
    if fmt == "sqlcipher-or-unknown":
        status = inspect_database(data_dir)
        if status["format"] == "sqlcipher":
            key, source = DatabaseKeyStore(data_dir).get()
            converted, skipped = _encrypt_migration_backups(data_dir, key)
            set_config_value(
                get_config_dir() / "hushclaw.toml",
                "memory.database_encryption",
                "sqlcipher",
            )
            harden_database_files(data_dir, DB_NAME)
            print("Database is already encrypted with SQLCipher.")
            print(f"  key source: {source}")
            print(
                f"  migration backups encrypted: {converted} "
                f"(already encrypted/skipped: {skipped})"
            )
            return 0
        raise DatabaseEncryptionError(status["open_error"] or "database is locked or corrupt")
    if fmt in {"missing", "empty"}:
        create_private_file(db_path)
        sqlite3.connect(db_path).close()

    # Apply the canonical schema/migration path while the source is still
    # plaintext. This makes the command safe to run directly, not only through
    # the installers that already perform their own preflight migration.
    from hushclaw.memory.db import open_db

    plain = open_db(data_dir, database_encryption="off")
    plain.close()

    get_sqlcipher_driver()
    store = DatabaseKeyStore(data_dir)
    key, source, created = store.get_or_create()
    started = time.monotonic()
    # Convert recovery snapshots first. An interruption is safely resumable:
    # converted snapshots are skipped on retry and the live database remains
    # plaintext until every historical copy is protected.
    converted, skipped = _encrypt_migration_backups(data_dir, key)
    _replace_with_encrypted(db_path, key)
    set_config_value(
        get_config_dir() / "hushclaw.toml", "memory.database_encryption", "sqlcipher"
    )
    harden_database_files(data_dir, DB_NAME)
    print(f"Encrypted database with SQLCipher in {time.monotonic() - started:.1f}s")
    print(f"  key source: {source}")
    print(f"  migration backups encrypted: {converted} (already encrypted/skipped: {skipped})")
    if source == "private-file":
        print("  warning: no platform credential vault was available; key uses an owner-only file")
    if created:
        print("  recovery key created; run `hushclaw database recovery-key` and store it offline")
    return 0


def cmd_database_recovery_key(args) -> int:
    from hushclaw.config.loader import load_config

    data_dir = load_config().memory.data_dir
    key, source = DatabaseKeyStore(data_dir).get()
    if not key:
        raise DatabaseEncryptionError(
            f"database key is unavailable; set {DATABASE_KEY_ENV} before retrying"
        )
    print(key)
    print(f"Key source: {source}", file=__import__("sys").stderr)
    return 0
