"""Dev-server detection and tunnel management capability."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from afk.capabilities.tunnel.base import DevServerConfig, TunnelProcessProtocol
from afk.capabilities.tunnel.cloudflared import CloudflaredTunnelProcess
from afk.capabilities.tunnel.config import (
    TunnelConfig,
    find_free_port,
    generate_tunnel_config,
    load_tunnel_config,
    save_tunnel_config,
)
from afk.capabilities.tunnel.expo import ExpoTunnelProcess
from afk.capabilities.tunnel.multi_service import MultiServiceTunnelProcess

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Project type detection
# ---------------------------------------------------------------------------


def _detect_package_manager(worktree: Path) -> list[str]:
    """Determine npm/yarn/pnpm from lock files."""
    if (worktree / "pnpm-lock.yaml").exists():
        return ["pnpm"]
    if (worktree / "yarn.lock").exists():
        return ["yarn"]
    return ["npm"]


def _detect_framework(pkg: dict, dev_script: str) -> str:
    """Return framework name based on package.json contents."""
    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    if "next" in all_deps:
        return "next"
    if "vite" in all_deps or "vite" in dev_script:
        return "vite"
    if "nuxt" in all_deps:
        return "nuxt"
    if "@angular/cli" in all_deps:
        return "angular"
    if "react-scripts" in all_deps:
        return "create-react-app"
    return "generic-npm"


def _build_port_args(framework: str, port: int) -> list[str]:
    """Return CLI args to override the dev-server port for *framework*."""
    # next dev -p PORT
    if framework == "next":
        return ["-p", str(port)]
    # create-react-app uses PORT env var (handled separately)
    if framework == "create-react-app":
        return []
    # vite / nuxt / angular / generic all accept --port PORT
    return ["--port", str(port)]


def _is_expo_project(worktree: Path, pkg: dict) -> bool:
    """Check if the project is an Expo (React Native) project.

    Requires ``expo`` in dependencies AND one of: ``app.json``,
    ``app.config.js``, or ``app.config.ts``.
    """
    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    if "expo" not in all_deps:
        return False
    config_files = ["app.json", "app.config.js", "app.config.ts"]
    return any((worktree / f).exists() for f in config_files)


def detect_dev_server(worktree_path: str) -> DevServerConfig | None:
    """Detect project type and return a DevServerConfig with a free port.

    Returns ``None`` when no supported project is found.
    """
    wt = Path(worktree_path)
    pkg_path = wt / "package.json"

    if not pkg_path.exists():
        return None

    try:
        pkg = json.loads(pkg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    # Check for Expo project first (may not have a "dev" script)
    if _is_expo_project(wt, pkg):
        port = find_free_port()
        pm = _detect_package_manager(wt)
        # Use npx expo start --tunnel --port PORT
        cmd = ["npx", "expo", "start", "--tunnel", "--port", str(port)]
        return DevServerConfig(command=cmd, port=port, framework="expo")

    # Standard web project — requires a "dev" script
    scripts = pkg.get("scripts", {})
    if "dev" not in scripts:
        return None

    dev_script = scripts["dev"]
    pm = _detect_package_manager(wt)
    framework = _detect_framework(pkg, dev_script)
    port = find_free_port()
    port_args = _build_port_args(framework, port)

    # e.g. ["npm", "run", "dev", "--", "--port", "9123"]
    cmd = [*pm, "run", "dev"]
    if port_args:
        cmd += ["--", *port_args]

    return DevServerConfig(command=cmd, port=port, framework=framework)


# ---------------------------------------------------------------------------
# Tunnel capability — session-level tunnel manager
# ---------------------------------------------------------------------------

class TunnelCapability:
    """Manages tunnels as a side-car to sessions.

    Tracks one tunnel process per session (keyed by channel_id).
    Dispatches to MultiServiceTunnelProcess (config-based),
    ExpoTunnelProcess, or CloudflaredTunnelProcess.
    """

    def __init__(self) -> None:
        self._tunnels: dict[str, TunnelProcessProtocol | MultiServiceTunnelProcess] = {}

    async def init_tunnel(self, worktree_path: str) -> TunnelConfig:
        """Use Claude CLI to analyze the project and generate .afk/tunnel.json.

        Returns the generated TunnelConfig. Raises RuntimeError on failure.
        """
        config = await generate_tunnel_config(worktree_path)
        save_tunnel_config(worktree_path, config)
        return config

    async def start_tunnel(
        self, channel_id: str, worktree_path: str,
    ) -> str | dict[str, str]:
        """Start tunnel(s). Returns public URL (str) or {name: url} dict.

        Priority:
        1. ``.afk/tunnel.json`` exists → multi-service
        2. Auto-detect → Expo or Cloudflared (single service)

        Raises RuntimeError on detection/startup failure.
        """
        # 1. Try config-based multi-service
        tunnel_config = load_tunnel_config(worktree_path)
        if tunnel_config:
            multi = MultiServiceTunnelProcess()
            urls = await multi.start(worktree_path, tunnel_config)
            self._tunnels[channel_id] = multi
            return urls

        # 2. Fallback: auto-detect single service
        config = detect_dev_server(worktree_path)
        if not config:
            raise RuntimeError(
                "Could not detect dev server.\n"
                'Ensure the worktree has a package.json with a "dev" script '
                "or is an Expo project with app.json.\n\n"
                "Tip: Use /tunnel init to generate a config for any project type."
            )

        tunnel: TunnelProcessProtocol
        if config.framework == "expo":
            tunnel = ExpoTunnelProcess()
        else:
            tunnel = CloudflaredTunnelProcess()

        url = await tunnel.start(worktree_path, config)
        self._tunnels[channel_id] = tunnel
        return url

    async def stop_tunnel(self, channel_id: str) -> bool:
        """Stop tunnel for a session. Returns True if a tunnel was stopped."""
        tunnel = self._tunnels.pop(channel_id, None)
        if not tunnel:
            return False
        await tunnel.stop()
        return True

    def get_tunnel(
        self, channel_id: str,
    ) -> TunnelProcessProtocol | MultiServiceTunnelProcess | None:
        """Get active tunnel for a session, or None."""
        tunnel = self._tunnels.get(channel_id)
        if tunnel and not tunnel.is_alive:
            del self._tunnels[channel_id]
            return None
        return tunnel

    async def cleanup_session(self, channel_id: str) -> None:
        """Called when session stops/completes — cleanup tunnel if any."""
        await self.stop_tunnel(channel_id)
