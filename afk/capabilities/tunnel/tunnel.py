"""Dev-server detection and cloudflared tunnel management capability."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Project type detection
# ---------------------------------------------------------------------------

@dataclass
class DevServerConfig:
    """Detected dev server command and port."""

    command: list[str]  # e.g. ["npm", "run", "dev", "--", "--port", "9123"]
    port: int
    framework: str  # e.g. "vite", "next", "generic-npm"


def _find_free_port() -> int:
    """Ask the OS for an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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

    scripts = pkg.get("scripts", {})
    if "dev" not in scripts:
        return None

    dev_script = scripts["dev"]
    pm = _detect_package_manager(wt)
    framework = _detect_framework(pkg, dev_script)
    port = _find_free_port()
    port_args = _build_port_args(framework, port)

    # e.g. ["npm", "run", "dev", "--", "--port", "9123"]
    cmd = [*pm, "run", "dev"]
    if port_args:
        cmd += ["--", *port_args]

    return DevServerConfig(command=cmd, port=port, framework=framework)


# ---------------------------------------------------------------------------
# Tunnel process management
# ---------------------------------------------------------------------------

class TunnelProcess:
    """Manages a dev-server subprocess + cloudflared tunnel subprocess."""

    def __init__(self) -> None:
        self._dev_server: asyncio.subprocess.Process | None = None
        self._cloudflared: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None
        self._config: DevServerConfig | None = None

    @property
    def public_url(self) -> str | None:
        return self._public_url

    @property
    def is_alive(self) -> bool:
        return (
            self._dev_server is not None
            and self._dev_server.returncode is None
            and self._cloudflared is not None
            and self._cloudflared.returncode is None
        )

    @property
    def config(self) -> DevServerConfig | None:
        return self._config

    async def start(self, worktree_path: str, server_config: DevServerConfig) -> str:
        """Start dev server + cloudflared.  Returns the public URL."""
        self._config = server_config

        cloudflared_path = shutil.which("cloudflared")
        if not cloudflared_path:
            raise RuntimeError(
                "cloudflared not found in PATH. Install: brew install cloudflared"
            )

        # Build env for create-react-app (PORT env var)
        env: dict[str, str] | None = None
        if server_config.framework == "create-react-app":
            import os
            env = {**os.environ, "PORT": str(server_config.port)}

        # 1. Start dev server
        self._dev_server = await asyncio.create_subprocess_exec(
            *server_config.command,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # 2. Wait for dev server to be ready (reads stdout+stderr, checks TCP port)
        await self._wait_for_dev_server()

        # 3. Start cloudflared
        self._cloudflared = await asyncio.create_subprocess_exec(
            cloudflared_path,
            "tunnel", "--url", f"http://localhost:{server_config.port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # 4. Parse public URL from cloudflared stderr
        self._public_url = await self._wait_for_tunnel_url()
        return self._public_url

    # ------------------------------------------------------------------

    async def _wait_for_dev_server(self, timeout: float = 30.0) -> None:
        """Wait until the dev server is ready (output keywords + TCP port check)."""
        if not self._dev_server:
            return

        port = self._config.port if self._config else None
        ready_keywords = ["ready", "started", "listening", "compiled", "localhost"]

        async def _read_stream(stream: asyncio.StreamReader | None, label: str) -> bool:
            """Read lines from a stream, return True if a ready keyword is found."""
            if not stream:
                return False
            while True:
                try:
                    line = await asyncio.wait_for(stream.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    return False
                if not line:
                    return False
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug("dev-server(%s): %s", label, text)
                if any(kw in text.lower() for kw in ready_keywords):
                    return True

        async def _check_port(p: int) -> bool:
            """Return True if TCP connection to localhost:p succeeds."""
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", p), timeout=1.0,
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (OSError, asyncio.TimeoutError):
                return False

        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout

            while loop.time() < deadline:
                # Read from both stdout and stderr concurrently
                tasks = [
                    asyncio.create_task(_read_stream(self._dev_server.stdout, "out")),
                    asyncio.create_task(_read_stream(self._dev_server.stderr, "err")),
                ]
                done, pending = await asyncio.wait(
                    tasks, timeout=2.0, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

                # If any stream found a ready keyword, verify with port check
                if any(t.result() for t in done if not t.cancelled()):
                    if port and await _check_port(port):
                        logger.info("Dev server ready (keyword + port %d open)", port)
                        return
                    # Keyword found but port not open yet — keep waiting
                    logger.debug("Ready keyword found but port %d not open yet", port)
                    continue

                # No keyword yet — try a direct TCP port check as fallback
                if port and await _check_port(port):
                    logger.info("Dev server ready (port %d open)", port)
                    return

                # Check if dev server process died
                if self._dev_server.returncode is not None:
                    logger.warning("Dev server exited with code %d", self._dev_server.returncode)
                    return

            logger.warning("Timed out waiting for dev server (port %s)", port)
        except Exception as e:
            logger.warning("Error waiting for dev server: %s", e)

    async def _wait_for_tunnel_url(self, timeout: float = 30.0) -> str:
        """Parse cloudflared stderr for the trycloudflare.com URL."""
        if not self._cloudflared or not self._cloudflared.stderr:
            raise RuntimeError("cloudflared process has no stderr")

        url_pattern = re.compile(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)")

        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    line = await asyncio.wait_for(
                        self._cloudflared.stderr.readline(),
                        timeout=min(remaining, 2.0),
                    )
                except asyncio.TimeoutError:
                    continue

                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()
                logger.debug("cloudflared: %s", text)

                match = url_pattern.search(text)
                if match:
                    url = match.group(1)
                    logger.info("Tunnel URL: %s", url)
                    return url
        except Exception as e:
            await self.stop()
            raise RuntimeError(f"Failed to parse tunnel URL: {e}")

        await self.stop()
        raise RuntimeError("Timed out waiting for cloudflared tunnel URL")

    async def stop(self) -> None:
        """Stop both subprocesses (cloudflared first, then dev server)."""
        for name, proc in [
            ("cloudflared", self._cloudflared),
            ("dev-server", self._dev_server),
        ]:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                logger.info("Stopped %s process", name)

        self._cloudflared = None
        self._dev_server = None
        self._public_url = None


# ---------------------------------------------------------------------------
# Tunnel capability — session-level tunnel manager
# ---------------------------------------------------------------------------

class TunnelCapability:
    """Manages tunnels as a side-car to sessions.

    Tracks one TunnelProcess per session (keyed by channel_id).
    """

    def __init__(self) -> None:
        self._tunnels: dict[str, TunnelProcess] = {}

    async def start_tunnel(self, channel_id: str, worktree_path: str) -> str:
        """Detect dev server and start tunnel. Returns public URL.

        Raises RuntimeError on detection/startup failure.
        """
        config = detect_dev_server(worktree_path)
        if not config:
            raise RuntimeError(
                "Could not detect dev server.\n"
                'Ensure the worktree has a package.json with a "dev" script.'
            )

        tunnel = TunnelProcess()
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

    def get_tunnel(self, channel_id: str) -> TunnelProcess | None:
        """Get active tunnel for a session, or None."""
        tunnel = self._tunnels.get(channel_id)
        if tunnel and not tunnel.is_alive:
            del self._tunnels[channel_id]
            return None
        return tunnel

    async def cleanup_session(self, channel_id: str) -> None:
        """Called when session stops/completes — cleanup tunnel if any."""
        await self.stop_tunnel(channel_id)
