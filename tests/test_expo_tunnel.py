"""Tests for Expo tunnel process and dispatch logic."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from afk.capabilities.tunnel.base import DevServerConfig, TunnelProcessProtocol
from afk.capabilities.tunnel.cloudflared import CloudflaredTunnelProcess
from afk.capabilities.tunnel.expo import ExpoTunnelProcess
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
