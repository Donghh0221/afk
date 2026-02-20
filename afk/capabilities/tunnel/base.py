"""Shared types for tunnel capability."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class DevServerConfig:
    """Detected dev server command and port."""

    command: list[str]  # e.g. ["npm", "run", "dev", "--", "--port", "9123"]
    port: int
    framework: str  # e.g. "vite", "next", "expo", "generic-npm"


@runtime_checkable
class TunnelProcessProtocol(Protocol):
    """Common interface for tunnel process implementations."""

    @property
    def public_url(self) -> str | None: ...

    @property
    def is_alive(self) -> bool: ...

    @property
    def config(self) -> DevServerConfig | None: ...

    @property
    def tunnel_type(self) -> str: ...

    async def start(self, worktree_path: str, server_config: DevServerConfig) -> str: ...

    async def stop(self) -> None: ...
