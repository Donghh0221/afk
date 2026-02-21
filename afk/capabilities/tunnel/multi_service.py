"""Multi-service tunnel process — run multiple services with individual tunnels."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass, field

import aiohttp

from afk.capabilities.tunnel.base import DevServerConfig
from afk.capabilities.tunnel.config import ServiceConfig, TunnelConfig, find_free_port

logger = logging.getLogger(__name__)

_CLOUDFLARED_URL_RE = re.compile(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)")


@dataclass
class RunningService:
    """Runtime state for a single service."""

    config: ServiceConfig
    port: int
    process: asyncio.subprocess.Process | None = None
    cloudflared: asyncio.subprocess.Process | None = None
    public_url: str | None = None

    @property
    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None


class MultiServiceTunnelProcess:
    """Manages multiple services + individual cloudflared tunnels.

    Duck-types with ``TunnelProcessProtocol`` for backward compatibility.
    """

    def __init__(self) -> None:
        self._services: list[RunningService] = []
        self._config_obj: DevServerConfig | None = None

    @property
    def public_url(self) -> str | None:
        """First tunnel URL (backward compat)."""
        for svc in self._services:
            if svc.public_url:
                return svc.public_url
        return None

    @property
    def public_urls(self) -> dict[str, str]:
        """All tunnel URLs keyed by service name."""
        return {
            svc.config.name: svc.public_url
            for svc in self._services
            if svc.public_url
        }

    @property
    def is_alive(self) -> bool:
        return any(svc.is_alive for svc in self._services)

    @property
    def config(self) -> DevServerConfig | None:
        return self._config_obj

    @property
    def tunnel_type(self) -> str:
        return "multi-service"

    @property
    def services(self) -> list[RunningService]:
        return list(self._services)

    async def start(
        self, worktree_path: str, tunnel_config: TunnelConfig,
    ) -> dict[str, str]:
        """Start all services sequentially. Returns {name: public_url} for tunneled services.

        Raises RuntimeError if all services fail to start.
        """
        self._config_obj = DevServerConfig(
            command=[], port=0, framework="multi-service",
        )

        cloudflared_path = shutil.which("cloudflared")
        urls: dict[str, str] = {}
        started = 0

        for svc_config in tunnel_config.services:
            port = find_free_port()
            running = RunningService(config=svc_config, port=port)

            try:
                # 1. Start the service process
                cmd = svc_config.resolve_command(port)
                cwd = svc_config.resolve_path(worktree_path)
                logger.info(
                    "Starting service %s: %s (port %d, cwd=%s)",
                    svc_config.name, cmd, port, cwd,
                )

                running.process = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                # 2. Wait for TCP port to open
                await self._wait_for_service(svc_config.name, port)
                started += 1

                # 3. Start cloudflared if requested
                if svc_config.tunnel and cloudflared_path:
                    running.cloudflared = await asyncio.create_subprocess_exec(
                        cloudflared_path,
                        "tunnel", "--url", f"http://localhost:{port}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    url = await self._wait_for_tunnel_url(
                        svc_config.name, running.cloudflared,
                    )
                    running.public_url = url
                    urls[svc_config.name] = url
                elif svc_config.tunnel and not cloudflared_path:
                    logger.warning(
                        "cloudflared not found — skipping tunnel for %s",
                        svc_config.name,
                    )

            except Exception as e:
                logger.warning("Failed to start service %s: %s", svc_config.name, e)
                # Clean up this failed service but continue with others
                await self._stop_running_service(running)

            self._services.append(running)

        if started == 0:
            raise RuntimeError("All services failed to start.")

        return urls

    async def stop(self) -> None:
        """Stop all services (cloudflared first, then processes)."""
        for svc in reversed(self._services):
            await self._stop_running_service(svc)
        self._services.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _wait_for_service(
        self, name: str, port: int, timeout: float = 30.0,
    ) -> None:
        """Wait for service to be ready (TCP port open + HTTP responding)."""
        deadline = asyncio.get_event_loop().time() + timeout
        port_open = False

        while asyncio.get_event_loop().time() < deadline:
            # 1. Wait for TCP port to open
            if not port_open:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", port), timeout=1.0,
                    )
                    writer.close()
                    await writer.wait_closed()
                    port_open = True
                    logger.debug("Service %s port %d open", name, port)
                except (OSError, asyncio.TimeoutError):
                    await asyncio.sleep(0.5)
                    continue

            # 2. Verify HTTP readiness (prevents 502 from cloudflared)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{port}/",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ):
                        logger.info("Service %s ready on port %d (HTTP OK)", name, port)
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                await asyncio.sleep(0.5)

        if port_open:
            # Port is open but HTTP not ready — proceed anyway (some services
            # may not respond on / but still work through cloudflared)
            logger.warning("Service %s port %d open but HTTP check failed, proceeding", name, port)
            return
        raise RuntimeError(f"Service {name} did not start on port {port} within {timeout}s")

    async def _wait_for_tunnel_url(
        self,
        name: str,
        proc: asyncio.subprocess.Process,
        timeout: float = 30.0,
    ) -> str:
        """Parse cloudflared stderr for the trycloudflare.com URL."""
        if not proc.stderr:
            raise RuntimeError(f"cloudflared for {name} has no stderr")

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            try:
                line = await asyncio.wait_for(
                    proc.stderr.readline(),
                    timeout=min(remaining, 2.0),
                )
            except asyncio.TimeoutError:
                continue

            if not line:
                break

            text = line.decode("utf-8", errors="replace").strip()
            logger.debug("cloudflared(%s): %s", name, text)

            match = _CLOUDFLARED_URL_RE.search(text)
            if match:
                url = match.group(1)
                logger.info("Tunnel URL for %s: %s", name, url)
                return url

        raise RuntimeError(f"Timed out waiting for cloudflared tunnel URL for {name}")

    async def _stop_running_service(self, svc: RunningService) -> None:
        """Stop a single RunningService (cloudflared first, then process)."""
        for label, proc in [
            (f"cloudflared({svc.config.name})", svc.cloudflared),
            (f"service({svc.config.name})", svc.process),
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
                logger.info("Stopped %s", label)

        svc.cloudflared = None
        svc.process = None
        svc.public_url = None
