"""Expo tunnel process — Metro dev server + cloudflared for React Native.

Starts ``npx expo start`` (without ``--tunnel``) and tunnels through
cloudflared instead of ngrok, which now requires paid authentication.
The cloudflared HTTPS URL is converted to ``exp://`` for Expo Go.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from urllib.parse import urlparse

import aiohttp

from afk.capabilities.tunnel.base import DevServerConfig
from afk.capabilities.tunnel.redirect import RedirectTunnel

logger = logging.getLogger(__name__)

_CLOUDFLARED_URL_RE = re.compile(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)")


class ExpoTunnelProcess:
    """Manages an Expo Metro dev server + cloudflared tunnel.

    The flow:
    1. ``npx expo start --port PORT`` (local Metro bundler)
    2. Wait for Metro to be ready (HTTP health check)
    3. ``cloudflared tunnel --url http://localhost:PORT`` → HTTPS URL
    4. Convert HTTPS → ``exp://`` for Expo Go
    5. Optional :class:`RedirectTunnel` for iOS inline-button deep link
    """

    def __init__(self) -> None:
        self._dev_server: asyncio.subprocess.Process | None = None
        self._cloudflared: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None
        self._config: DevServerConfig | None = None
        self._redirect: RedirectTunnel | None = None

    @property
    def public_url(self) -> str | None:
        return self._public_url

    @property
    def redirect_url(self) -> str | None:
        """HTTPS redirect URL (cloudflared → exp://), or None."""
        if self._redirect and self._redirect.is_alive:
            return self._redirect.public_url
        return None

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
        return "expo"

    async def start(self, worktree_path: str, server_config: DevServerConfig) -> str:
        """Start Expo dev server + cloudflared tunnel. Returns exp:// URL."""
        self._config = server_config

        cloudflared_path = shutil.which("cloudflared")
        if not cloudflared_path:
            raise RuntimeError(
                "cloudflared not found in PATH. Install: brew install cloudflared"
            )

        # 1. Start Metro dev server (CI=1 prevents interactive prompts)
        env = {**os.environ, "CI": "1"}
        self._dev_server = await asyncio.create_subprocess_exec(
            *server_config.command,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # 2. Wait for Metro to be ready
        await self._wait_for_dev_server()

        # 3. Start cloudflared tunnel
        self._cloudflared = await asyncio.create_subprocess_exec(
            cloudflared_path,
            "tunnel", "--url", f"http://localhost:{server_config.port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # 4. Parse HTTPS URL from cloudflared
        self._public_url = await self._wait_for_tunnel_url()
        logger.info("Expo tunnel URL: %s", self._public_url)

        # 5. Start HTTPS redirect tunnel for iOS one-tap (exp:// deep link)
        exp_url = self._https_to_exp(self._public_url)
        self._redirect = RedirectTunnel()
        await self._redirect.start(exp_url)

        return self._public_url

    # ------------------------------------------------------------------

    async def _wait_for_dev_server(self, timeout: float = 30.0) -> None:
        """Wait until Metro dev server responds to HTTP requests."""
        if not self._dev_server:
            return

        port = self._config.port if self._config else None
        ready_keywords = ["ready", "started", "listening", "bundler", "localhost", "metro"]

        async def _read_stream(stream: asyncio.StreamReader | None, label: str) -> bool:
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
                    logger.debug("expo(%s): %s", label, text)
                if any(kw in text.lower() for kw in ready_keywords):
                    return True

        async def _check_http(p: int) -> bool:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{p}/",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        return True
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                return False

        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            port_ready = False

            while loop.time() < deadline:
                tasks = [
                    asyncio.create_task(_read_stream(self._dev_server.stdout, "out")),
                    asyncio.create_task(_read_stream(self._dev_server.stderr, "err")),
                ]
                done, pending = await asyncio.wait(
                    tasks, timeout=2.0, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

                if not port_ready and port:
                    port_ready = await _check_http(port)
                    if port_ready:
                        logger.info("Expo Metro dev server ready on port %d", port)
                        return

                if self._dev_server.returncode is not None:
                    logger.warning("Expo process exited with code %d", self._dev_server.returncode)
                    return

            logger.warning("Timed out waiting for Expo dev server (port %s)", port)
        except Exception as e:
            logger.warning("Error waiting for Expo dev server: %s", e)

    async def _wait_for_tunnel_url(self, timeout: float = 30.0) -> str:
        """Parse cloudflared stderr for the trycloudflare.com URL."""
        if not self._cloudflared or not self._cloudflared.stderr:
            raise RuntimeError("cloudflared process has no stderr")

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
            logger.debug("expo-cloudflared: %s", text)

            match = _CLOUDFLARED_URL_RE.search(text)
            if match:
                return match.group(1)

        await self.stop()
        raise RuntimeError("Timed out waiting for cloudflared tunnel URL")

    @staticmethod
    def _https_to_exp(url: str) -> str:
        """Convert ``https://host.trycloudflare.com`` → ``exp://host.trycloudflare.com``."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"exp://{host}{port}"

    async def stop(self) -> None:
        """Stop redirect tunnel, cloudflared, and Expo dev server."""
        if self._redirect:
            await self._redirect.stop()
            self._redirect = None

        for name, proc in [
            ("cloudflared", self._cloudflared),
            ("expo", self._dev_server),
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
