from __future__ import annotations

import json
from pathlib import Path

import pytest

from afk.storage.template_store import TemplateConfig, TemplateStore


def _make_template(base: Path, name: str, desc: str = "test") -> Path:
    """Helper to create a template directory with template.json and a scaffold file."""
    d = base / name
    d.mkdir(parents=True)
    meta = {"name": name, "description": desc}
    (d / "template.json").write_text(json.dumps(meta))
    (d / "scaffold.txt").write_text(f"scaffold for {name}")
    return d


class TestTemplateStore:
    def test_get_exact_name(self, tmp_path: Path):
        _make_template(tmp_path, "coding")
        store = TemplateStore(tmp_path)
        t = store.get("coding")
        assert t is not None
        assert t.name == "coding"

    def test_get_case_insensitive(self, tmp_path: Path):
        _make_template(tmp_path, "Research")
        store = TemplateStore(tmp_path)
        assert store.get("research") is not None
        assert store.get("RESEARCH") is not None

    def test_get_nonexistent(self, tmp_path: Path):
        _make_template(tmp_path, "a")
        store = TemplateStore(tmp_path)
        assert store.get("nope") is None

    def test_list_all(self, tmp_path: Path):
        _make_template(tmp_path, "t1")
        _make_template(tmp_path, "t2")
        store = TemplateStore(tmp_path)
        all_templates = store.list_all()
        assert len(all_templates) == 2
        assert "t1" in all_templates
        assert "t2" in all_templates

    def test_apply_copies_files(self, tmp_path: Path):
        tpl_dir = _make_template(tmp_path / "templates", "mytemplate")
        store = TemplateStore(tmp_path / "templates")
        tpl = store.get("mytemplate")

        dest = tmp_path / "dest"
        dest.mkdir()
        TemplateStore.apply(tpl, str(dest))

        assert (dest / "scaffold.txt").exists()
        assert (dest / "scaffold.txt").read_text() == "scaffold for mytemplate"

    def test_apply_excludes_template_json(self, tmp_path: Path):
        _make_template(tmp_path / "templates", "t")
        store = TemplateStore(tmp_path / "templates")
        tpl = store.get("t")

        dest = tmp_path / "dest"
        dest.mkdir()
        TemplateStore.apply(tpl, str(dest))

        assert not (dest / "template.json").exists()

    def test_apply_subdirectory(self, tmp_path: Path):
        tpl_dir = tmp_path / "templates" / "withsub"
        tpl_dir.mkdir(parents=True)
        (tpl_dir / "template.json").write_text(json.dumps({"name": "withsub"}))
        sub = tpl_dir / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested content")

        store = TemplateStore(tmp_path / "templates")
        tpl = store.get("withsub")

        dest = tmp_path / "dest"
        dest.mkdir()
        TemplateStore.apply(tpl, str(dest))

        assert (dest / "subdir" / "nested.txt").exists()
        assert (dest / "subdir" / "nested.txt").read_text() == "nested content"

    def test_load_nonexistent_directory(self, tmp_path: Path):
        store = TemplateStore(tmp_path / "no-such-dir")
        assert store.list_all() == {}

    def test_load_invalid_json(self, tmp_path: Path):
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "template.json").write_text("not valid json {{{")
        store = TemplateStore(tmp_path)
        # The invalid template should be skipped
        assert store.get("bad") is None
