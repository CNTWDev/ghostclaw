"""SQLCipher connection and platform-backed database-key management."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from hushclaw.core.storage_security import create_private_file, ensure_private_dir, ensure_private_file


SQLITE_HEADER = b"SQLite format 3\x00"
DATABASE_KEY_ENV = "HUSHCLAW_DATABASE_KEY"
KEYCHAIN_SERVICE = "com.hushclaw.database"
FILE_KEY_NAME = ".database-key"
DPAPI_KEY_NAME = ".database-key.dpapi"
_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")

try:  # Keep the embeddable core dependency-free.
    from sqlcipher3 import dbapi2 as _sqlcipher_driver
except ImportError:  # pragma: no cover - depends on the optional runtime extra
    _sqlcipher_driver = None


def _dbapi_exception_types(name: str) -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [getattr(sqlite3, name)]
    if _sqlcipher_driver is not None:
        candidate = getattr(_sqlcipher_driver, name)
        if candidate not in types:
            types.append(candidate)
    return tuple(types)


DATABASE_ERRORS = _dbapi_exception_types("Error")
INTEGRITY_ERRORS = _dbapi_exception_types("IntegrityError")
OPERATIONAL_ERRORS = _dbapi_exception_types("OperationalError")


class DatabaseEncryptionError(RuntimeError):
    pass


def database_file_format(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return "missing"
    with path.open("rb") as handle:
        header = handle.read(len(SQLITE_HEADER))
    if not header:
        return "empty"
    if header == SQLITE_HEADER:
        return "sqlite-plaintext"
    return "sqlcipher-or-unknown"


def validate_database_key(value: str) -> str:
    key = str(value or "").strip()
    if not _KEY_RE.fullmatch(key):
        raise DatabaseEncryptionError(
            "database recovery key must contain exactly 64 hexadecimal characters"
        )
    return key.lower()


def _key_account(data_dir: Path) -> str:
    digest = hashlib.sha256(str(Path(data_dir).expanduser().resolve()).encode()).hexdigest()[:24]
    return f"hushclaw-{digest}"


class DatabaseKeyStore:
    """Use the native credential vault when available, with an explicit file fallback."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = ensure_private_dir(Path(data_dir))
        self.account = _key_account(self.data_dir)
        self.file_path = self.data_dir / FILE_KEY_NAME
        self.dpapi_path = self.data_dir / DPAPI_KEY_NAME

    @staticmethod
    def _powershell() -> str:
        if sys.platform != "win32":
            return ""
        return shutil.which("powershell.exe") or shutil.which("pwsh.exe") or ""

    def _windows_get(self) -> str:
        powershell = self._powershell()
        if not powershell or not self.dpapi_path.exists():
            return ""
        ensure_private_file(self.dpapi_path)
        script = (
            "$raw=[Console]::In.ReadToEnd().Trim();"
            "$bytes=[Convert]::FromBase64String($raw);"
            "$plain=[Security.Cryptography.ProtectedData]::Unprotect("
            "$bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
            "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($plain))"
        )
        proc = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            input=self.dpapi_path.read_text(encoding="ascii"),
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def _windows_set(self, key: str) -> bool:
        powershell = self._powershell()
        if not powershell:
            return False
        script = (
            "$raw=[Console]::In.ReadToEnd().Trim();"
            "$bytes=[Text.Encoding]::UTF8.GetBytes($raw);"
            "$sealed=[Security.Cryptography.ProtectedData]::Protect("
            "$bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
            "[Console]::Out.Write([Convert]::ToBase64String($sealed))"
        )
        proc = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            input=key,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return False
        create_private_file(self.dpapi_path)
        self.dpapi_path.write_text(proc.stdout.strip() + "\n", encoding="ascii")
        ensure_private_file(self.dpapi_path)
        return True

    def _macos_get(self) -> str:
        if sys.platform != "darwin" or shutil.which("security") is None:
            return ""
        proc = subprocess.run(
            ["security", "find-generic-password", "-a", self.account, "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def _macos_set(self, key: str) -> bool:
        if sys.platform != "darwin" or shutil.which("security") is None:
            return False
        proc = subprocess.run(
            [
                "security", "add-generic-password", "-U", "-a", self.account,
                "-s", KEYCHAIN_SERVICE, "-l", "HushClaw Database Key", "-w", key,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            # Sandboxed/headless installers may not be allowed to modify the
            # login keychain. Fall through to the explicit owner-only file
            # backend; status/doctor will surface the weaker key source.
            return False
        return True

    def _linux_get(self) -> str:
        if not sys.platform.startswith("linux") or shutil.which("secret-tool") is None:
            return ""
        proc = subprocess.run(
            ["secret-tool", "lookup", "application", "hushclaw", "account", self.account],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def _linux_set(self, key: str) -> bool:
        if not sys.platform.startswith("linux") or shutil.which("secret-tool") is None:
            return False
        proc = subprocess.run(
            [
                "secret-tool", "store", "--label", "HushClaw Database Key",
                "application", "hushclaw", "account", self.account,
            ],
            input=key,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return False
        return True

    def get(self) -> tuple[str, str]:
        env_key = os.environ.get(DATABASE_KEY_ENV, "").strip()
        if env_key:
            return validate_database_key(env_key), "environment"
        for getter, source in (
            (self._macos_get, "macos-keychain"),
            (self._windows_get, "windows-dpapi"),
            (self._linux_get, "secret-service"),
        ):
            value = getter()
            if value:
                return validate_database_key(value), source
        if self.file_path.exists():
            ensure_private_file(self.file_path)
            return validate_database_key(self.file_path.read_text(encoding="ascii").strip()), "private-file"
        return "", "missing"

    def set(self, key: str) -> str:
        key = validate_database_key(key)
        if self._macos_set(key):
            return "macos-keychain"
        if self._windows_set(key):
            return "windows-dpapi"
        if self._linux_set(key):
            return "secret-service"
        create_private_file(self.file_path)
        self.file_path.write_text(key + "\n", encoding="ascii")
        ensure_private_file(self.file_path)
        return "private-file"

    def get_or_create(self) -> tuple[str, str, bool]:
        key, source = self.get()
        if key:
            return key, source, False
        key = secrets.token_hex(32)
        return key, self.set(key), True


def get_sqlcipher_driver():
    if _sqlcipher_driver is None:
        raise DatabaseEncryptionError(
            "SQLCipher driver is not installed; install with: pip install 'hushclaw[encryption]'"
        )
    return _sqlcipher_driver


def sqlcipher_available() -> bool:
    try:
        get_sqlcipher_driver()
    except DatabaseEncryptionError:
        return False
    return True


def connect_sqlcipher(
    path: Path,
    key: str,
    *,
    readonly: bool = False,
    isolation_level=None,
    check_same_thread: bool = False,
):
    key = validate_database_key(key)
    path = Path(path)
    if readonly and not path.exists():
        raise FileNotFoundError(path)
    if not path.exists():
        create_private_file(path)
    driver = get_sqlcipher_driver()
    conn = driver.connect(
        str(path),
        check_same_thread=check_same_thread,
        isolation_level=isolation_level,
    )
    try:
        # The raw random key contains only validated hex, so it cannot alter the PRAGMA.
        conn.execute(f'''PRAGMA key = "x'{key}'"''')
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        if readonly:
            conn.execute("PRAGMA query_only = ON")
        conn.row_factory = driver.Row
        return conn
    except Exception as exc:
        conn.close()
        raise DatabaseEncryptionError(
            "could not unlock SQLCipher database; the recovery key may be missing or incorrect"
        ) from exc


def connect_database(
    data_dir: Path,
    *,
    mode: str = "auto",
    readonly: bool = False,
    isolation_level=None,
    check_same_thread: bool = False,
    key: str = "",
):
    """Open plaintext SQLite or SQLCipher according to the file and policy."""
    import sqlite3

    data_dir = ensure_private_dir(Path(data_dir))
    path = data_dir / "memory.db"
    fmt = database_file_format(path)
    if mode not in {"auto", "off", "sqlcipher"}:
        raise DatabaseEncryptionError(f"unsupported database encryption mode: {mode}")

    encrypted = fmt == "sqlcipher-or-unknown"
    if mode == "off" and encrypted:
        raise DatabaseEncryptionError(
            "database is encrypted but memory.database_encryption is off"
        )
    if mode == "sqlcipher" and fmt == "sqlite-plaintext":
        raise DatabaseEncryptionError(
            "database is still plaintext; run `hushclaw database encrypt` before startup"
        )
    if mode == "sqlcipher" and fmt in {"missing", "empty"}:
        encrypted = True

    if encrypted:
        if not key:
            store = DatabaseKeyStore(data_dir)
            key, _source = store.get()
            if not key and fmt in {"missing", "empty"}:
                key, _source, _created = store.get_or_create()
            if not key:
                raise DatabaseEncryptionError(
                    f"database key is unavailable; set {DATABASE_KEY_ENV} or restore it to the credential vault"
                )
        conn = connect_sqlcipher(
            path,
            key,
            readonly=readonly,
            isolation_level=isolation_level,
            check_same_thread=check_same_thread,
        )
        return conn, True, key

    if fmt == "missing":
        create_private_file(path)
    if readonly:
        uri = f"file:{path.resolve()}?mode=ro"
        conn = sqlite3.connect(
            uri, uri=True, check_same_thread=check_same_thread, isolation_level=isolation_level
        )
    else:
        conn = sqlite3.connect(
            str(path), check_same_thread=check_same_thread, isolation_level=isolation_level
        )
    return conn, False, ""


def key_status(data_dir: Path) -> tuple[bool, str]:
    try:
        key, source = DatabaseKeyStore(data_dir).get()
    except DatabaseEncryptionError:
        return False, "invalid"
    return bool(key), source
