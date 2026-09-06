"""Tests for backup export/import helpers."""
from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path

from hushclaw.cli.backup import create_backup_archive, restore_backup_archive


def _make_sqlite_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO sample(note) VALUES ('hello backup')")
        conn.commit()
    finally:
        conn.close()


def test_create_backup_archive_includes_config_data_and_plugins(tmp_path):
    config_file = tmp_path / "config" / "hushclaw.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[memory]\ndata_dir = \"/tmp/hushclaw-data\"\n", encoding="utf-8")

    data_dir = tmp_path / "data-src"
    data_dir.mkdir()
    _make_sqlite_db(data_dir / "memory.db")
    (data_dir / "uploads").mkdir()
    (data_dir / "uploads" / "artifact.txt").write_text("payload", encoding="utf-8")

    plugin_dir = tmp_path / "config" / "tools"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "example.py").write_text("print('tool')\n", encoding="utf-8")

    archive = tmp_path / "backup.zip"
    create_backup_archive(
        archive,
        config_file=config_file,
        data_dir=data_dir,
        plugin_dir=plugin_dir,
    )

    assert archive.exists()
    if os.name == "posix":
        assert archive.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "config/hushclaw.toml" in names
        assert "config/tools/example.py" in names
        assert "data/memory.db" in names
        assert "data/uploads/artifact.txt" in names

        extracted_db = tmp_path / "extracted.db"
        extracted_db.write_bytes(zf.read("data/memory.db"))

    conn = sqlite3.connect(extracted_db)
    try:
        row = conn.execute("SELECT note FROM sample").fetchone()
        assert row[0] == "hello backup"
    finally:
        conn.close()


def test_restore_backup_archive_populates_destinations(tmp_path):
    src_config = tmp_path / "src-config" / "hushclaw.toml"
    src_config.parent.mkdir(parents=True, exist_ok=True)
    src_config.write_text("[provider]\nname = \"anthropic-raw\"\n", encoding="utf-8")

    src_data = tmp_path / "src-data"
    src_data.mkdir()
    _make_sqlite_db(src_data / "memory.db")
    (src_data / "sessions").mkdir()
    (src_data / "sessions" / "state.txt").write_text("kept", encoding="utf-8")

    src_plugins = tmp_path / "src-config" / "tools"
    src_plugins.mkdir(parents=True, exist_ok=True)
    (src_plugins / "local_tool.py").write_text("print('ok')\n", encoding="utf-8")

    archive = tmp_path / "backup.zip"
    create_backup_archive(
        archive,
        config_file=src_config,
        data_dir=src_data,
        plugin_dir=src_plugins,
    )

    dest_config = tmp_path / "dest-config" / "hushclaw.toml"
    dest_data = tmp_path / "dest-data"
    dest_plugins = tmp_path / "dest-config" / "tools"

    manifest = restore_backup_archive(
        archive,
        config_file=dest_config,
        data_dir=dest_data,
        plugin_dir=dest_plugins,
    )

    assert manifest["format"] == "hushclaw-backup"
    assert dest_config.read_text(encoding="utf-8").startswith("[provider]")
    assert (dest_data / "sessions" / "state.txt").read_text(encoding="utf-8") == "kept"
    assert (dest_plugins / "local_tool.py").exists()

    conn = sqlite3.connect(dest_data / "memory.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_restore_backup_archive_refuses_existing_targets_without_force(tmp_path):
    src_config = tmp_path / "src-config" / "hushclaw.toml"
    src_config.parent.mkdir(parents=True, exist_ok=True)
    src_config.write_text("[agent]\nmodel = \"claude-sonnet-4-6\"\n", encoding="utf-8")

    src_data = tmp_path / "src-data"
    src_data.mkdir()
    _make_sqlite_db(src_data / "memory.db")

    archive = tmp_path / "backup.zip"
    create_backup_archive(
        archive,
        config_file=src_config,
        data_dir=src_data,
        plugin_dir=None,
    )

    dest_config = tmp_path / "dest-config" / "hushclaw.toml"
    dest_config.parent.mkdir(parents=True, exist_ok=True)
    dest_config.write_text("existing = true\n", encoding="utf-8")

    dest_data = tmp_path / "dest-data"
    dest_data.mkdir()
    (dest_data / "placeholder.txt").write_text("busy", encoding="utf-8")

    try:
        restore_backup_archive(
            archive,
            config_file=dest_config,
            data_dir=dest_data,
            plugin_dir=None,
        )
        assert False, "restore should have refused to overwrite existing targets"
    except FileExistsError as e:
        assert "config file" in str(e)
