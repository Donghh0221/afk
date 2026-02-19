"""Event renderer for Telegram.

Subscribes to agent events from the EventBus and renders them
as Telegram messages via the ControlPlanePort interface.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from afk.core.events import (
    AgentAssistantEvent,
    AgentInputRequestEvent,
    AgentPermissionRequestEvent,
    AgentResultEvent,
    AgentStoppedEvent,
    AgentSystemEvent,
    EventBus,
    EventLevel,
)
from afk.dashboard.message_store import MessageStore

# ---------------------------------------------------------------------------
# Level-to-behavior mapping for Telegram control plane
# ---------------------------------------------------------------------------

_SKIP = "skip"            # Not sent to Telegram
_STORE_ONLY = "store"     # Stored in message_store, not sent to Telegram
_SILENT = "silent"        # Stored + sent with silent=True (no notification)
_NORMAL = "normal"        # Stored + sent with normal notification

_LEVEL_BEHAVIOR: dict[EventLevel, str] = {
    EventLevel.INTERNAL: _SKIP,
    EventLevel.PROGRESS: _STORE_ONLY,
    EventLevel.INFO: _SILENT,
    EventLevel.NOTIFY: _NORMAL,
}

if TYPE_CHECKING:
    from afk.ports.control_plane import ControlPlanePort

logger = logging.getLogger(__name__)


class EventRenderer:
    """Subscribes to EventBus events and renders them to a ControlPlanePort.

    Replaces the _handle_claude_message / _handle_assistant_message
    logic from the old Orchestrator.
    """

    def __init__(
        self,
        event_bus: EventBus,
        messenger: ControlPlanePort,
        message_store: MessageStore,
    ) -> None:
        self._bus = event_bus
        self._messenger = messenger
        self._ms = message_store
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        """Start background tasks that consume events."""
        self._tasks.append(asyncio.create_task(self._render_system_events()))
        self._tasks.append(asyncio.create_task(self._render_assistant_events()))
        self._tasks.append(asyncio.create_task(self._render_result_events()))
        self._tasks.append(asyncio.create_task(self._render_stopped_events()))
        self._tasks.append(asyncio.create_task(self._render_permission_request_events()))
        self._tasks.append(asyncio.create_task(self._render_input_request_events()))

    def stop(self) -> None:
        """Cancel all background tasks."""
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    async def _render_system_events(self) -> None:
        """Render AgentSystemEvent — session ready."""
        try:
            async for ev in self._bus.iter_events(AgentSystemEvent):
                self._ms.append(
                    ev.channel_id, "system",
                    f"Session ready (id={ev.agent_session_id})"
                    if ev.agent_session_id
                    else "System message",
                )
        except asyncio.CancelledError:
            pass

    async def _render_assistant_events(self) -> None:
        """Render AgentAssistantEvent — text, tool use, tool results."""
        try:
            async for ev in self._bus.iter_events(AgentAssistantEvent):
                try:
                    await self._render_assistant(ev)
                except Exception:
                    logger.exception(
                        "Error rendering assistant event for %s",
                        ev.session_name,
                    )
        except asyncio.CancelledError:
            pass

    async def _render_result_events(self) -> None:
        """Render AgentResultEvent — task complete with cost/duration."""
        try:
            async for ev in self._bus.iter_events(AgentResultEvent):
                cost_usd = ev.cost_usd
                duration_ms = ev.duration_ms
                duration_s = duration_ms / 1000 if duration_ms else 0

                cost_text = f"${cost_usd:.4f}" if cost_usd else ""
                time_text = f"{duration_s:.1f}s" if duration_s else ""
                info_parts = [p for p in [cost_text, time_text] if p]
                info = f" ({', '.join(info_parts)})" if info_parts else ""

                meta = {}
                if cost_usd:
                    meta["cost"] = cost_usd
                if duration_s:
                    meta["duration"] = duration_s
                self._ms.append(ev.channel_id, "result", f"Done{info}", meta=meta)

                await self._messenger.send_message(
                    ev.channel_id, f"✅ Done{info}"
                )
        except asyncio.CancelledError:
            pass

    async def _render_stopped_events(self) -> None:
        """Render AgentStoppedEvent — agent process stopped unexpectedly."""
        try:
            async for ev in self._bus.iter_events(AgentStoppedEvent):
                try:
                    await self._messenger.send_message(
                        ev.channel_id,
                        f"⚠️ Session ended: {ev.session_name}\n"
                        f"Use /new in General to start a new session.",
                    )
                except Exception:
                    pass
                try:
                    await self._messenger.close_session_channel(ev.channel_id)
                except Exception:
                    logger.warning(
                        "Failed to delete topic for %s", ev.session_name
                    )
        except asyncio.CancelledError:
            pass

    async def _render_permission_request_events(self) -> None:
        """Render AgentPermissionRequestEvent — tool permission with Allow/Deny buttons."""
        try:
            async for ev in self._bus.iter_events(AgentPermissionRequestEvent):
                try:
                    tool_args = _summarize_tool_args(ev.tool_input)
                    self._ms.append(
                        ev.channel_id, "permission",
                        f"⚠️ Permission: {ev.tool_name} — {tool_args}",
                    )
                    await self._messenger.send_permission_request(
                        ev.channel_id, ev.tool_name, tool_args, ev.request_id,
                    )
                except Exception:
                    logger.exception(
                        "Error rendering permission request for %s",
                        ev.request_id,
                    )
        except asyncio.CancelledError:
            pass

    async def _render_input_request_events(self) -> None:
        """Render AgentInputRequestEvent — agent ready for user input."""
        try:
            async for ev in self._bus.iter_events(AgentInputRequestEvent):
                try:
                    self._ms.append(ev.channel_id, "system", "Ready for input")
                    await self._messenger.send_message(
                        ev.channel_id, "💬 Ready for your input", silent=True,
                    )
                except Exception:
                    logger.exception(
                        "Error rendering input request for %s",
                        ev.session_name,
                    )
        except asyncio.CancelledError:
            pass

    async def _render_assistant(self, ev: AgentAssistantEvent) -> None:
        """Process assistant content blocks using level-based dispatch."""
        behavior = _LEVEL_BEHAVIOR[ev.level]

        # Verbose override: PROGRESS → send silently instead of store-only
        if behavior == _STORE_ONLY and ev.verbose:
            behavior = _SILENT

        if behavior == _SKIP:
            return

        content_blocks = ev.content_blocks

        if isinstance(content_blocks, str):
            if content_blocks:
                self._ms.append(ev.channel_id, "assistant", content_blocks)
                if behavior != _STORE_ONLY:
                    await self._messenger.send_message(
                        ev.channel_id, content_blocks,
                        silent=(behavior == _SILENT),
                    )
            return

        texts: list[str] = []
        tool_lines: list[str] = []
        result_lines: list[str] = []

        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text", "")
                if text:
                    texts.append(text)

            elif block_type == "tool_use":
                tool_name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                args_str = _summarize_tool_args(tool_input)
                tool_lines.append(f"🔧 {tool_name}: {args_str}")

            elif block_type == "tool_result":
                result_text = _summarize_tool_result(block)
                if result_text:
                    result_lines.append(result_text)

        silent = (behavior == _SILENT)
        should_send = behavior not in (_SKIP, _STORE_ONLY)

        if texts:
            text_body = "\n".join(texts)
            self._ms.append(ev.channel_id, "assistant", text_body)
            if should_send:
                await self._messenger.send_message(
                    ev.channel_id, text_body, silent=silent
                )
        if tool_lines:
            tool_body = "\n".join(tool_lines)
            self._ms.append(ev.channel_id, "tool", tool_body)
            if should_send:
                await self._messenger.send_message(
                    ev.channel_id, tool_body, silent=silent
                )
        if result_lines:
            result_body = "\n".join(result_lines)
            self._ms.append(ev.channel_id, "tool", result_body)
            if should_send:
                await self._messenger.send_message(
                    ev.channel_id, result_body, silent=silent
                )


# ---------------------------------------------------------------------------
# Helpers (extracted from old Orchestrator)
# ---------------------------------------------------------------------------

def _summarize_tool_args(tool_input: dict | str) -> str:
    """Summarize tool arguments into human-readable form."""
    if isinstance(tool_input, str):
        return tool_input[:300]
    if isinstance(tool_input, dict):
        if "command" in tool_input:
            return tool_input["command"]
        elif "content" in tool_input:
            return tool_input["content"][:200]
        elif "file_path" in tool_input:
            return tool_input["file_path"]
        else:
            return str(tool_input)[:300]
    return str(tool_input)[:300]


def _summarize_tool_result(block: dict) -> str:
    """Convert tool_result block into human-readable summary."""
    content = block.get("content")
    is_error = block.get("is_error", False)
    prefix = "❌ Tool error" if is_error else "📎 Tool result"

    if not content:
        return ""

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts)
    else:
        text = str(content)

    if not text.strip():
        return ""

    if len(text) > 500:
        text = text[:500] + "…"

    return f"{prefix}: {text}"
