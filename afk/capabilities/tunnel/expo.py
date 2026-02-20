"""Expo tunnel process — npx expo start --tunnel for React Native projects."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from afk.capabilities.tunnel.base import DevServerConfig

logger = logging.getLogger(__name__)


class ExpoTunnelProcess:
    """Manages a single ``npx expo start --tunnel`` subprocess.

    Unlike cloudflared, Expo handles both the dev server and tunnel in one
    process (via the ``@expo/ngrok`` package).
    """

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None
        self._config: DevServerConfig | None = None

    @property
    def public_url(self) -> str | None:
        return self._public_url

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def config(self) -> DevServerConfig | None:
        return self._config

    @property
    def tunnel_type(self) -> str:
        return "expo"

    async def start(self, worktree_path: str, server_config: DevServerConfig) -> str:
        """Start Expo dev server with tunnel. Returns the public URL."""
        self._config = server_config

        # Ensure @expo/ngrok is installed (required for --tunnel)
        await self._ensure_ngrok(worktree_path)

        # CI=1 prevents interactive prompts
        env = {**os.environ, "CI": "1"}

        self._process = await asyncio.create_subprocess_exec(
            *server_config.command,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Parse tunnel URL from output
        self._public_url = await self._wait_for_tunnel_url()
        return self._public_url

    async def _ensure_ngrok(self, worktree_path: str) -> None:
        """Install @expo/ngrok if not already present."""
        try:
            check = await asyncio.create_subprocess_exec(
                "npx", "--yes", "@expo/ngrok", "--version",
                cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(check.wait(), timeout=30)
            if check.returncode == 0:
                return
        except (asyncio.TimeoutError, OSError):
            pass

        logger.info("Installing @expo/ngrok for tunnel support...")
        install = await asyncio.create_subprocess_exec(
            "npx", "--yes", "expo", "install", "@expo/ngrok",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(install.wait(), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("Timed out installing @expo/ngrok")
        if install.returncode != 0:
            logger.warning("@expo/ngrok install exited with code %d", install.returncode or -1)

    async def _wait_for_tunnel_url(self, timeout: float = 60.0) -> str:
        """Parse Expo output for the tunnel URL.

        Looks for patterns like:
        - ``exp://...exp.direct`` (Expo Go deep link)
        - Falls back to querying ngrok local API at 127.0.0.1:4040
        """
        if not self._process:
            raise RuntimeError("Expo process not started")

        # Match Expo tunnel URL patterns
        url_pattern = re.compile(r"(exp://[^\s]+\.exp\.direct(?::\d+)?)")
        # Also match the Metro bundler URL that Expo prints with tunnel
        metro_tunnel_pattern = re.compile(r"(https?://[^\s]+\.ngrok[^\s]*)")

        async def _read_stream(stream: asyncio.StreamReader | None, label: str) -> str | None:
            """Read lines looking for tunnel URL."""
            if not stream:
                return None
            while True:
                try:
                    line = await asyncio.wait_for(stream.readline(), timeout=2.0)
                except asyncio.TimeoutError:
                    return None
                if not line:
                    return None
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug("expo(%s): %s", label, text)

                match = url_pattern.search(text)
                if match:
                    return match.group(1)

                match = metro_tunnel_pattern.search(text)
                if match:
                    return match.group(1)
            return None

        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout

            while loop.time() < deadline:
                # Read from both stdout and stderr concurrently
                tasks = [
                    asyncio.create_task(_read_stream(self._process.stdout, "out")),
                    asyncio.create_task(_read_stream(self._process.stderr, "err")),
                ]
                done, pending = await asyncio.wait(
                    tasks, timeout=5.0, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

                for t in done:
                    if not t.cancelled():
                        result = t.result()
                        if result:
                            logger.info("Expo tunnel URL: %s", result)
                            return result

                # Check if process died
                if self._process.returncode is not None:
                    break

                # Fallback: query ngrok API
                url = await self._query_ngrok_api()
                if url:
                    logger.info("Expo tunnel URL (via ngrok API): %s", url)
                    return url

        except Exception as e:
            await self.stop()
            raise RuntimeError(f"Failed to get Expo tunnel URL: {e}")

        await self.stop()
        raise RuntimeError("Timed out waiting for Expo tunnel URL")

    async def _query_ngrok_api(self) -> str | None:
        """Try to get the tunnel URL from ngrok's local API."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "http://127.0.0.1:4040/api/tunnels",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            if proc.returncode != 0:
                return None
            data = json.loads(stdout.decode("utf-8", errors="replace"))
            tunnels = data.get("tunnels", [])
            if tunnels:
                return tunnels[0].get("public_url")
        except (asyncio.TimeoutError, json.JSONDecodeError, OSError, KeyError):
            pass
        return None

    async def stop(self) -> None:
        """Stop the Expo process."""
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            logger.info("Stopped Expo tunnel process")

        self._process = None
        self._public_url = None
