from __future__ import annotations

from typing import Protocol, Callable, Awaitable


class MessengerPort(Protocol):
    """Messenger abstract interface. Core logic depends only on this protocol."""

    async def send_message(
        self, channel_id: str, text: str, *, silent: bool = False
    ) -> str:
        """Send a message. silent=True sends without notification. Returns: message ID."""
        ...

    async def edit_message(
        self, channel_id: str, message_id: str, text: str
    ) -> None:
        """Edit an existing message."""
        ...

    async def send_permission_request(
        self,
        channel_id: str,
        tool_name: str,
        tool_args: str,
        request_id: str,
    ) -> None:
        """Display permission approval request with buttons."""
        ...

    async def create_session_channel(self, name: str) -> str:
        """Create a session-dedicated channel. Returns: channel_id."""
        ...

    def get_channel_link(self, channel_id: str) -> str | None:
        """Return a deep-link URL to the channel, or None if unsupported."""
        ...

    async def close_session_channel(self, channel_id: str) -> None:
        """Delete/close a session-dedicated channel."""
        ...

    async def download_voice(self, file_id: str) -> str:
        """Download a voice message file. Returns: local file path."""
        ...

    async def start(self) -> None:
        """Start messenger connection (polling, etc.)."""
        ...

    async def stop(self) -> None:
        """Stop messenger connection."""
        ...
