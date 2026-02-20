"""Tests for tunnel detection helper functions."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from afk.capabilities.tunnel.tunnel import (
    _build_port_args,
    _detect_framework,
    _detect_package_manager,
    detect_dev_server,
)


class TestDetectFramework:
    def test_next(self):
        pkg = {"dependencies": {"next": "14.0.0"}}
        assert _detect_framework(pkg, "next dev") == "next"

    def test_vite_in_deps(self):
        pkg = {"devDependencies": {"vite": "5.0"}}
        assert _detect_framework(pkg, "vite") == "vite"

    def test_vite_in_dev_script(self):
        pkg = {"dependencies": {}}
        assert _detect_framework(pkg, "vite dev") == "vite"

    def test_nuxt(self):
        pkg = {"dependencies": {"nuxt": "3.0"}}
        assert _detect_framework(pkg, "nuxt dev") == "nuxt"

    def test_angular(self):
        pkg = {"devDependencies": {"@angular/cli": "17.0"}}
        assert _detect_framework(pkg, "ng serve") == "angular"

    def test_react_scripts(self):
        pkg = {"dependencies": {"react-scripts": "5.0"}}
        assert _detect_framework(pkg, "react-scripts start") == "create-react-app"

    def test_generic(self):
        pkg = {"dependencies": {"express": "4.0"}}
        assert _detect_framework(pkg, "node server.js") == "generic-npm"

    def test_empty_deps(self):
        assert _detect_framework({}, "") == "generic-npm"


class TestBuildPortArgs:
    def test_next(self):
        assert _build_port_args("next", 3000) == ["-p", "3000"]

    def test_create_react_app(self):
        assert _build_port_args("create-react-app", 3000) == []

    def test_vite(self):
        assert _build_port_args("vite", 5173) == ["--port", "5173"]

    def test_generic(self):
        assert _build_port_args("generic-npm", 8080) == ["--port", "8080"]


class TestDetectPackageManager:
    def test_pnpm(self, tmp_path: Path):
        (tmp_path / "pnpm-lock.yaml").touch()
        assert _detect_package_manager(tmp_path) == ["pnpm"]

    def test_yarn(self, tmp_path: Path):
        (tmp_path / "yarn.lock").touch()
        assert _detect_package_manager(tmp_path) == ["yarn"]

    def test_npm_default(self, tmp_path: Path):
        assert _detect_package_manager(tmp_path) == ["npm"]

    def test_pnpm_takes_priority_over_yarn(self, tmp_path: Path):
        (tmp_path / "pnpm-lock.yaml").touch()
        (tmp_path / "yarn.lock").touch()
        assert _detect_package_manager(tmp_path) == ["pnpm"]


class TestDetectDevServer:
    def test_no_package_json(self, tmp_path: Path):
        assert detect_dev_server(str(tmp_path)) is None

    def test_no_dev_script(self, tmp_path: Path):
        pkg = {"name": "test", "scripts": {"build": "tsc"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert detect_dev_server(str(tmp_path)) is None

    def test_invalid_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{bad json")
        assert detect_dev_server(str(tmp_path)) is None

    def test_detection_success(self, tmp_path: Path):
        pkg = {
            "name": "test",
            "scripts": {"dev": "next dev"},
            "dependencies": {"next": "14.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        config = detect_dev_server(str(tmp_path))
        assert config is not None
        assert config.framework == "next"
        assert config.port > 0
        assert config.command[0] == "npm"
        assert "-p" in config.command

    def test_detection_with_yarn(self, tmp_path: Path):
        pkg = {
            "name": "test",
            "scripts": {"dev": "vite"},
            "devDependencies": {"vite": "5.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "yarn.lock").touch()
        config = detect_dev_server(str(tmp_path))
        assert config is not None
        assert config.framework == "vite"
        assert config.command[0] == "yarn"
