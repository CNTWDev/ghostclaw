"""Filesystem protection for local HushClaw state.

This is the first (and dependency-free) storage-security layer. It prevents
other operating-system accounts from traversing the data directory or reading
SQLite files and backups. It is intentionally not described as encryption:
processes running as the same login user can still read plaintext files.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def _chmod(path: Path, mode: int) -> None:
    """Apply a private POSIX mode when the platform exposes POSIX permissions."""
    if os.name != "posix" or not path.exists() or path.is_symlink():
        return
    try:
        os.chmod(path, mode)
    except OSError:
        # The subsequent open operation will surface an actionable error when
        # this is a read-only or externally-owned path.
        pass


def ensure_private_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    _chmod(path, PRIVATE_DIR_MODE)
    return path


def ensure_private_file(path: Path) -> Path:
    path = Path(path)
    if path.exists():
        _chmod(path, PRIVATE_FILE_MODE)
    return path


def create_private_file(path: Path) -> Path:
    """Create an empty owner-only file without a world-readable creation window."""
    path = Path(path)
    ensure_private_dir(path.parent)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, PRIVATE_FILE_MODE)
    except FileExistsError:
        ensure_private_file(path)
    else:
        os.close(fd)
        ensure_private_file(path)
    return path


def harden_database_files(data_dir: Path, db_name: str = "memory.db") -> None:
    """Protect the SQLite database, its sidecars, and migration backups."""
    data_dir = ensure_private_dir(Path(data_dir))
    for name in (db_name, f"{db_name}-wal", f"{db_name}-shm"):
        ensure_private_file(data_dir / name)

    backup_root = data_dir / "backups"
    if backup_root.exists():
        _chmod(backup_root, PRIVATE_DIR_MODE)
        db_backups = backup_root / "memory-db"
        if db_backups.exists():
            _chmod(db_backups, PRIVATE_DIR_MODE)
            for path in db_backups.iterdir():
                if path.is_file() and not path.is_symlink():
                    ensure_private_file(path)


def harden_private_tree(root: Path) -> None:
    """Make a restored private-state tree owner-only.

    This is used at restore/migration boundaries rather than every startup,
    keeping normal startup cost independent of artifact count.
    """
    root = Path(root)
    if not root.exists():
        return
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        _chmod(current_path, PRIVATE_DIR_MODE)
        for name in dirs:
            _chmod(current_path / name, PRIVATE_DIR_MODE)
        for name in files:
            ensure_private_file(current_path / name)


def posix_mode(path: Path) -> int | None:
    """Return permission bits for diagnostics, or None off POSIX/missing paths."""
    path = Path(path)
    if os.name != "posix" or not path.exists():
        return None
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def is_private_mode(path: Path, *, directory: bool) -> bool | None:
    mode = posix_mode(path)
    if mode is None:
        return None
    expected = PRIVATE_DIR_MODE if directory else PRIVATE_FILE_MODE
    # Owner may have fewer rights; group/other must have none.
    return (mode & 0o077) == 0 and (mode & expected) == mode
