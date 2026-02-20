from __future__ import annotations

import json
from pathlib import Path

import pytest

from afk.storage.project_store import ProjectStore


class TestProjectStore:
    def test_add_success(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        store = ProjectStore(data_dir)
        assert store.add("myproject", str(project_dir)) is True
        assert store.get("myproject") is not None
        assert store.get("myproject")["path"] == str(project_dir)

    def test_add_duplicate_returns_false(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "dup"
        project_dir.mkdir()
        store = ProjectStore(data_dir)
        store.add("dup", str(project_dir))
        assert store.add("dup", str(project_dir)) is False

    def test_add_duplicate_case_insensitive(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        store = ProjectStore(data_dir)
        store.add("MyProject", str(project_dir))
        assert store.add("myproject", str(project_dir)) is False

    def test_add_nonexistent_path_raises(self, data_dir: Path):
        store = ProjectStore(data_dir)
        with pytest.raises(ValueError, match="Directory not found"):
            store.add("bad", "/no/such/directory/ever")

    def test_remove_success(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "rm"
        project_dir.mkdir()
        store = ProjectStore(data_dir)
        store.add("rm", str(project_dir))
        assert store.remove("rm") is True
        assert store.get("rm") is None

    def test_remove_nonexistent(self, data_dir: Path):
        store = ProjectStore(data_dir)
        assert store.remove("nope") is False

    def test_get_case_insensitive(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "ci"
        project_dir.mkdir()
        store = ProjectStore(data_dir)
        store.add("CamelCase", str(project_dir))
        assert store.get("camelcase") is not None
        assert store.get("CAMELCASE") is not None

    def test_list_all(self, data_dir: Path, tmp_path: Path):
        d1 = tmp_path / "a"
        d1.mkdir()
        d2 = tmp_path / "b"
        d2.mkdir()
        store = ProjectStore(data_dir)
        store.add("a", str(d1))
        store.add("b", str(d2))
        projects = store.list_all()
        assert "a" in projects
        assert "b" in projects

    def test_save_load_roundtrip(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "roundtrip"
        project_dir.mkdir()
        store1 = ProjectStore(data_dir)
        store1.add("roundtrip", str(project_dir))

        # Create new store from same data_dir — should load persisted data
        store2 = ProjectStore(data_dir)
        assert store2.get("roundtrip") is not None
        assert store2.get("roundtrip")["path"] == str(project_dir)
