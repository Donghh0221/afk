from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class AgentPort(Protocol):
    """Abstract interface for agent runtimes.

    Any agent (Claude Code, Codex, Aider, etc.) must implement this protocol.
    Core logic depends only on this interface — never on concrete implementations.
    """

    @property
    def session_id(self) -> str | None:
        """Agent-internal session ID (for resume support)."""
        ...

    @property
    def is_alive(self) -> bool:
        """Whether the agent process is currently running."""
        ...

    async def start(
        self, working_dir: str, session_id: str | None = None
    ) -> None:
        """Start the agent process in the given working directory."""
        ...

    async def send_message(self, text: str) -> None:
        """Send user input to the agent."""
        ...

    async def send_permission_response(
        self, request_id: str, allowed: bool
    ) -> None:
        """Respond to a tool permission request."""
        ...

    async def read_responses(self) -> AsyncIterator[dict]:
        """Stream agent output events."""
        ...

    async def stop(self) -> None:
        """Stop the agent process."""
        ...
