"""Event Bus and typed event definitions for AFK core.

All agent output flows as events. Control planes subscribe and render.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class EventBus:
    """Simple in-process asyncio pub/sub event bus.

    Publishers call publish(event). Subscribers receive events via
    subscribe() which returns an asyncio.Queue, or iter_events() for
    convenient async iteration.
    """

    def __init__(self) -> None:
        self._subscribers: dict[type, list[asyncio.Queue]] = {}

    def subscribe(self, event_type: type[T]) -> asyncio.Queue[T]:
        """Subscribe to events of a specific type. Returns a Queue."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(event_type, []).append(queue)
        return queue

    def unsubscribe(self, event_type: type[T], queue: asyncio.Queue) -> None:
        """Remove a subscription."""
        queues = self._subscribers.get(event_type, [])
        if queue in queues:
            queues.remove(queue)

    def publish(self, event: object) -> None:
        """Publish an event to all subscribers of its type."""
        for queue in self._subscribers.get(type(event), []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full, dropping %s", type(event).__name__)

    async def iter_events(self, event_type: type[T]) -> AsyncIterator[T]:
        """Async iterator for events of a specific type."""
        queue = self.subscribe(event_type)
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(event_type, queue)


# ---------------------------------------------------------------------------
# Event level — semantic classification of agent actions
# ---------------------------------------------------------------------------

class EventLevel(Enum):
    """Semantic importance level of an agent event.

    Assigned at event creation time based on the nature of the agent action.
    Control planes map levels to their own rendering strategies.
    """
    INTERNAL = "internal"   # System internals (session init)
    PROGRESS = "progress"   # Tool use, intermediate work
    INFO = "info"           # Agent text output
    NOTIFY = "notify"       # Task completion, session lifecycle


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentSystemEvent:
    """Agent session ready (e.g. Claude system init message)."""
    channel_id: str
    agent_session_id: str | None
    level: EventLevel = EventLevel.INTERNAL


@dataclass(frozen=True)
class AgentAssistantEvent:
    """Agent produced assistant output (text, tool use, tool result blocks)."""
    channel_id: str
    content_blocks: list  # list[dict] — raw content blocks from agent
    session_name: str
    level: EventLevel
    verbose: bool  # session metadata — renderers may use for presentation


@dataclass(frozen=True)
class AgentResultEvent:
    """Agent completed a task."""
    channel_id: str
    cost_usd: float
    duration_ms: int
    level: EventLevel = EventLevel.NOTIFY


@dataclass(frozen=True)
class AgentStoppedEvent:
    """Agent process stopped unexpectedly."""
    channel_id: str
    session_name: str
    level: EventLevel = EventLevel.NOTIFY


@dataclass(frozen=True)
class SessionCreatedEvent:
    """A new session was created."""
    channel_id: str
    session_name: str
    project_name: str
    project_path: str
    worktree_path: str
    verbose: bool
