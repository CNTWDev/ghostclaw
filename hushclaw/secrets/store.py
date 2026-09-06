"""Local secret store.

The first backend is a small JSON file with 0600 permissions.  Config files
store stable secret references; this file stores the actual values.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from hushclaw.core.storage_security import ensure_private_dir, ensure_private_file
from hushclaw.paths import get_data_dir


class FileSecretStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_data_dir() / "secrets.json")

    def get(self, key: str, default: str = "") -> str:
        data = self._read()
        value = data.get(str(key), default)
        return value if isinstance(value, str) else default

    def set(self, key: str, value: str) -> None:
        key = str(key).strip()
        if not key:
            raise ValueError("secret key cannot be empty")
        data = self._read()
        data[key] = str(value)
        self._write(data)

    def delete(self, key: str) -> None:
        data = self._read()
        data.pop(str(key), None)
        self._write(data)

    def is_set(self, key: str) -> bool:
        return bool(self.get(key, ""))

    def _read(self) -> dict:
        ensure_private_dir(self.path.parent)
        ensure_private_file(self.path)
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        ensure_private_dir(self.path.parent)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def get_secret_store() -> FileSecretStore:
    return FileSecretStore()
