"""Cloudflared tunnel process — dev server + cloudflared quick tunnel."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil

import aiohttp

from afk.capabilities.tunnel.base import DevServerConfig

logger = logging.getLogger(__name__)


class CloudflaredTunnelProcess:
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

    @property
    def tunnel_type(self) -> str:
        return "cloudflared"

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
        """Wait until the dev server is ready (output keywords + HTTP check)."""
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

        async def _check_http(p: int) -> bool:
            """Return True if the dev server responds to an HTTP GET."""
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{p}/",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        # Any HTTP response means the server is serving
                        return True
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                return False

        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            port_open = False

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

                keyword_found = any(
                    t.result() for t in done if not t.cancelled()
                )

                if not port_open and port:
                    port_open = await _check_port(port)

                # Once port is open, verify HTTP readiness
                if port_open:
                    if await _check_http(port):
                        logger.info("Dev server ready (HTTP responds on port %d)", port)
                        return
                    if keyword_found:
                        logger.debug("Port %d open + keyword found but HTTP not ready yet", port)

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
