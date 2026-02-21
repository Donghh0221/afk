"""Tests for Expo tunnel process and dispatch logic."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from afk.capabilities.tunnel.base import DevServerConfig, TunnelProcessProtocol
from afk.capabilities.tunnel.cloudflared import CloudflaredTunnelProcess
from afk.capabilities.tunnel.expo import ExpoTunnelProcess
from afk.capabilities.tunnel.multi_service import MultiServiceTunnelProcess
from afk.capabilities.tunnel.tunnel import TunnelCapability


class TestProtocolConformance:
    """Verify both process types satisfy TunnelProcessProtocol."""

    def test_cloudflared_is_protocol(self):
        proc = CloudflaredTunnelProcess()
        assert isinstance(proc, TunnelProcessProtocol)

    def test_expo_is_protocol(self):
        proc = ExpoTunnelProcess()
        assert isinstance(proc, TunnelProcessProtocol)

    def test_cloudflared_tunnel_type(self):
        proc = CloudflaredTunnelProcess()
        assert proc.tunnel_type == "cloudflared"

    def test_expo_tunnel_type(self):
        proc = ExpoTunnelProcess()
        assert proc.tunnel_type == "expo"

    def test_initial_state_cloudflared(self):
        proc = CloudflaredTunnelProcess()
        assert proc.public_url is None
        assert proc.is_alive is False
        assert proc.config is None

    def test_initial_state_expo(self):
        proc = ExpoTunnelProcess()
        assert proc.public_url is None
        assert proc.is_alive is False
        assert proc.config is None


class TestTunnelCapabilityDispatch:
    """Verify TunnelCapability dispatches to the correct process type."""

    @pytest.mark.asyncio
    async def test_expo_project_dispatches_expo_process(self, tmp_path: Path):
        """framework=expo should create ExpoTunnelProcess."""
        pkg = {
            "name": "test-expo",
            "dependencies": {"expo": "~52.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "app.json").write_text('{"expo": {}}')

        cap = TunnelCapability()

        with (
            patch.object(ExpoTunnelProcess, "start", new_callable=AsyncMock) as mock_start,
            patch.object(ExpoTunnelProcess, "is_alive", new_callable=lambda: property(lambda self: True)),
        ):
            mock_start.return_value = "exp://test.exp.direct"
            url = await cap.start_tunnel("ch1", str(tmp_path))

        assert url == "exp://test.exp.direct"
        # Verify the stored tunnel type (bypass is_alive check via _tunnels directly)
        assert "ch1" in cap._tunnels
        assert cap._tunnels["ch1"].tunnel_type == "expo"

    @pytest.mark.asyncio
    async def test_web_project_dispatches_cloudflared_process(self, tmp_path: Path):
        """framework=next should create CloudflaredTunnelProcess."""
        pkg = {
            "name": "test-next",
            "scripts": {"dev": "next dev"},
            "dependencies": {"next": "14.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        cap = TunnelCapability()

        with (
            patch.object(CloudflaredTunnelProcess, "start", new_callable=AsyncMock) as mock_start,
            patch.object(CloudflaredTunnelProcess, "is_alive", new_callable=lambda: property(lambda self: True)),
        ):
            mock_start.return_value = "https://test.trycloudflare.com"
            url = await cap.start_tunnel("ch2", str(tmp_path))

        assert url == "https://test.trycloudflare.com"
        # Verify the stored tunnel type (bypass is_alive check via _tunnels directly)
        assert "ch2" in cap._tunnels
        assert cap._tunnels["ch2"].tunnel_type == "cloudflared"

    @pytest.mark.asyncio
    async def test_no_project_raises(self, tmp_path: Path):
        """Missing package.json should raise RuntimeError."""
        cap = TunnelCapability()
        with pytest.raises(RuntimeError, match="Could not detect dev server"):
            await cap.start_tunnel("ch3", str(tmp_path))

    @pytest.mark.asyncio
    async def test_stop_tunnel(self, tmp_path: Path):
        """stop_tunnel should call stop() on the process and remove it."""
        pkg = {
            "name": "test-expo",
            "dependencies": {"expo": "~52.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "app.json").write_text('{"expo": {}}')

        cap = TunnelCapability()

        with patch.object(ExpoTunnelProcess, "start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = "exp://test.exp.direct"
            await cap.start_tunnel("ch4", str(tmp_path))

        with patch.object(ExpoTunnelProcess, "stop", new_callable=AsyncMock) as mock_stop:
            result = await cap.stop_tunnel("ch4")

        assert result is True
        assert cap.get_tunnel("ch4") is None

    @pytest.mark.asyncio
    async def test_cleanup_session(self, tmp_path: Path):
        """cleanup_session should stop the tunnel."""
        pkg = {
            "name": "test-expo",
            "dependencies": {"expo": "~52.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "app.json").write_text('{"expo": {}}')

        cap = TunnelCapability()

        with patch.object(ExpoTunnelProcess, "start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = "exp://test.exp.direct"
            await cap.start_tunnel("ch5", str(tmp_path))

        with patch.object(ExpoTunnelProcess, "stop", new_callable=AsyncMock):
            await cap.cleanup_session("ch5")

        assert cap.get_tunnel("ch5") is None


class TestSingleServiceTunnelJsonOptimization:
    """Single-service tunnel.json should use auto-detect (Expo/Cloudflared)."""

    @pytest.mark.asyncio
    async def test_single_service_expo_uses_expo_process(self, tmp_path: Path):
        """1-service tunnel.json + Expo project → ExpoTunnelProcess."""
        pkg = {
            "name": "test-expo",
            "dependencies": {"expo": "~52.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "app.json").write_text('{"expo": {}}')

        # Create single-service tunnel.json
        afk_dir = tmp_path / ".afk"
        afk_dir.mkdir()
        (afk_dir / "tunnel.json").write_text(json.dumps({
            "services": [
                {"name": "expo", "command": "npx expo start --port {port}", "path": ".", "tunnel": True}
            ]
        }))

        cap = TunnelCapability()

        with (
            patch.object(ExpoTunnelProcess, "start", new_callable=AsyncMock) as mock_start,
            patch.object(ExpoTunnelProcess, "is_alive", new_callable=lambda: property(lambda self: True)),
        ):
            mock_start.return_value = "https://test.trycloudflare.com"
            url = await cap.start_tunnel("ch10", str(tmp_path))

        assert url == "https://test.trycloudflare.com"
        assert isinstance(cap._tunnels["ch10"], ExpoTunnelProcess)

    @pytest.mark.asyncio
    async def test_multi_service_uses_multi_process(self, tmp_path: Path):
        """2-service tunnel.json → MultiServiceTunnelProcess."""
        pkg = {
            "name": "test-app",
            "dependencies": {"expo": "~52.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "app.json").write_text('{"expo": {}}')

        afk_dir = tmp_path / ".afk"
        afk_dir.mkdir()
        (afk_dir / "tunnel.json").write_text(json.dumps({
            "services": [
                {"name": "web", "command": "npm run dev --port {port}", "path": ".", "tunnel": True},
                {"name": "api", "command": "uvicorn main:app --port {port}", "path": "./api", "tunnel": True},
            ]
        }))

        cap = TunnelCapability()

        with patch.object(MultiServiceTunnelProcess, "start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"web": "https://a.trycloudflare.com", "api": "https://b.trycloudflare.com"}
            result = await cap.start_tunnel("ch11", str(tmp_path))

        assert isinstance(result, dict)
        assert isinstance(cap._tunnels["ch11"], MultiServiceTunnelProcess)

    @pytest.mark.asyncio
    async def test_single_service_fallback_when_no_autodetect(self, tmp_path: Path):
        """1-service tunnel.json + no auto-detect → fallback to MultiServiceTunnelProcess."""
        # No package.json → auto-detect fails
        afk_dir = tmp_path / ".afk"
        afk_dir.mkdir()
        (afk_dir / "tunnel.json").write_text(json.dumps({
            "services": [
                {"name": "app", "command": "python -m http.server {port}", "path": ".", "tunnel": True}
            ]
        }))

        cap = TunnelCapability()

        with patch.object(MultiServiceTunnelProcess, "start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"app": "https://c.trycloudflare.com"}
            result = await cap.start_tunnel("ch12", str(tmp_path))

        assert isinstance(result, dict)
        assert isinstance(cap._tunnels["ch12"], MultiServiceTunnelProcess)
