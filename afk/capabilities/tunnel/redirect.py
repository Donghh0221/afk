"""Redirect tunnel — HTTPS → exp:// redirect for iOS deep linking.

Telegram inline buttons only support http/https/tg URLs.  This module
spins up a tiny aiohttp server that serves a redirect page (JS + meta
refresh) and exposes it via a cloudflared quick tunnel so that users can
open Expo Go with a single tap from Telegram on iOS.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import socket

from aiohttp import web

from afk.core.subprocess_tracker import track, untrack

logger = logging.getLogger(__name__)

_REDIRECT_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={exp_url}">
<title>Opening Expo Go…</title>
</head><body>
<p>Opening Expo Go…</p>
<p><a href="{exp_url}">Tap here if not redirected</a></p>
<script>window.location.href='{exp_url}';</script>
</body></html>
"""

_CLOUDFLARED_URL_RE = re.compile(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class RedirectTunnel:
    """HTTPS redirect proxy: aiohttp server + cloudflared quick tunnel.

    ``start()`` returns the public HTTPS URL (or ``None`` on failure).
    The server responds to ``GET /`` with a page that immediately
    redirects the browser to the given ``exp://`` URL.
    """

    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None
        self._cloudflared: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None

    @property
    def public_url(self) -> str | None:
        return self._public_url

    @property
    def is_alive(self) -> bool:
        return (
            self._runner is not None
            and self._cloudflared is not None
            and self._cloudflared.returncode is None
        )

    async def start(self, exp_url: str) -> str | None:
        """Start redirect server + cloudflared.  Returns HTTPS URL or None."""
        cloudflared_path = shutil.which("cloudflared")
        if not cloudflared_path:
            logger.info("cloudflared not found — skipping redirect tunnel")
            return None

        port = _find_free_port()

        try:
            # 1. Start aiohttp redirect server
            html = _REDIRECT_HTML_TEMPLATE.format(exp_url=exp_url)
            app = web.Application()
            app.router.add_get("/", lambda _req: web.Response(
                text=html, content_type="text/html",
            ))
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            site = web.TCPSite(self._runner, "127.0.0.1", port)
            await site.start()
            logger.info("Redirect server listening on port %d", port)

            # 2. Start cloudflared quick tunnel
            self._cloudflared = await asyncio.create_subprocess_exec(
                cloudflared_path,
                "tunnel", "--url", f"http://localhost:{port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            track(self._cloudflared.pid)

            # 3. Parse public URL from cloudflared stderr
            self._public_url = await self._wait_for_tunnel_url()
            logger.info("Redirect tunnel URL: %s", self._public_url)
            return self._public_url

        except Exception as e:
            logger.warning("Failed to start redirect tunnel: %s", e)
            await self.stop()
            return None

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
            logger.debug("redirect-cloudflared: %s", text)

            match = _CLOUDFLARED_URL_RE.search(text)
            if match:
                return match.group(1)

        raise RuntimeError("Timed out waiting for redirect tunnel URL")

    async def stop(self) -> None:
        """Shut down cloudflared + aiohttp server."""
        if self._cloudflared and self._cloudflared.returncode is None:
            pid = self._cloudflared.pid
            try:
                self._cloudflared.terminate()
                await asyncio.wait_for(self._cloudflared.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._cloudflared.kill()
                except ProcessLookupError:
                    pass
            untrack(pid)
            logger.info("Stopped redirect cloudflared process")

        if self._runner:
            await self._runner.cleanup()
            logger.info("Stopped redirect server")

        self._cloudflared = None
        self._runner = None
        self._public_url = None
