"""Tests for SessionManager core logic (classification + event publishing)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from afk.core.events import (
    AgentAssistantEvent,
    AgentInputRequestEvent,
    AgentPermissionRequestEvent,
    AgentResultEvent,
    AgentSystemEvent,
    EventBus,
    EventLevel,
    FileReadyEvent,
)
from afk.core.session_manager import Session, SessionManager


class TestClassifyAssistantLevel:
    def test_text_block_returns_info(self):
        blocks = [{"type": "text", "text": "hello"}]
        assert SessionManager._classify_assistant_level(blocks) == EventLevel.INFO

    def test_tool_use_only_returns_progress(self):
        blocks = [{"type": "tool_use", "name": "Bash", "input": {}}]
        assert SessionManager._classify_assistant_level(blocks) == EventLevel.PROGRESS

    def test_tool_result_only_returns_progress(self):
        blocks = [{"type": "tool_result", "content": "ok"}]
        assert SessionManager._classify_assistant_level(blocks) == EventLevel.PROGRESS

    def test_mixed_text_and_tool_returns_info(self):
        blocks = [
            {"type": "tool_use", "name": "Bash", "input": {}},
            {"type": "text", "text": "done"},
        ]
        assert SessionManager._classify_assistant_level(blocks) == EventLevel.INFO

    def test_string_returns_info(self):
        assert SessionManager._classify_assistant_level("plain text") == EventLevel.INFO

    def test_empty_list_returns_progress(self):
        assert SessionManager._classify_assistant_level([]) == EventLevel.PROGRESS


def _make_session(channel_id: str = "ch1", event_bus: EventBus | None = None) -> tuple[SessionManager, Session]:
    """Create a minimal SessionManager + Session for event publishing tests."""
    bus = event_bus or EventBus()
    messenger = MagicMock()
    sm = SessionManager(
        messenger=messenger,
        data_dir=MagicMock(),
        event_bus=bus,
    )

    agent = MagicMock()
    agent.is_alive = True
    agent.session_id = None
    agent.send_permission_response = AsyncMock()

    session = Session(
        name="test-session",
        project_name="proj",
        project_path="/tmp/proj",
        worktree_path="/tmp/wt",
        channel_id=channel_id,
        agent=agent,
    )
    session._session_logger = None

    sm._sessions[channel_id] = session
    return sm, session


class TestPublishAgentEvent:
    async def test_system_event(self):
        bus = EventBus()
        queue = bus.subscribe(AgentSystemEvent)
        sm, session = _make_session(event_bus=bus)

        await sm._publish_agent_event(session, {
            "type": "system",
            "session_id": "sid-123",
        })

        ev = queue.get_nowait()
        assert ev.channel_id == "ch1"
        assert ev.agent_session_id == "sid-123"
        assert session.agent_session_id == "sid-123"
        assert session.state == "idle"

    async def test_assistant_event(self):
        bus = EventBus()
        queue = bus.subscribe(AgentAssistantEvent)
        sm, session = _make_session(event_bus=bus)

        await sm._publish_agent_event(session, {
            "type": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        })

        ev = queue.get_nowait()
        assert ev.channel_id == "ch1"
        assert ev.level == EventLevel.INFO
        assert session.state == "running"

    async def test_permission_request_normal(self):
        bus = EventBus()
        queue = bus.subscribe(AgentPermissionRequestEvent)
        sm, session = _make_session(event_bus=bus)

        await sm._publish_agent_event(session, {
            "type": "permission_request",
            "tool_name": "Bash",
            "id": "req-1",
            "tool_input": {"command": "rm -rf /"},
        })

        ev = queue.get_nowait()
        assert ev.tool_name == "Bash"
        assert ev.request_id == "req-1"
        assert session.state == "waiting_permission"

    async def test_permission_request_auto_approve(self):
        bus = EventBus()
        queue = bus.subscribe(AgentPermissionRequestEvent)
        sm, session = _make_session(event_bus=bus)

        await sm._publish_agent_event(session, {
            "type": "permission_request",
            "tool_name": "ExitPlanMode",
            "id": "req-2",
            "tool_input": {},
        })

        # Should NOT publish to the queue (auto-approved)
        assert queue.empty()
        session.agent.send_permission_response.assert_awaited_once_with("req-2", True)
        assert session.state == "running"

    async def test_result_event(self):
        bus = EventBus()
        q_result = bus.subscribe(AgentResultEvent)
        q_input = bus.subscribe(AgentInputRequestEvent)
        sm, session = _make_session(event_bus=bus)

        await sm._publish_agent_event(session, {
            "type": "result",
            "total_cost_usd": 0.05,
            "duration_ms": 1234,
        })

        result_ev = q_result.get_nowait()
        assert result_ev.cost_usd == 0.05
        assert result_ev.duration_ms == 1234

        # Result should also emit AgentInputRequestEvent
        input_ev = q_input.get_nowait()
        assert input_ev.channel_id == "ch1"
        assert session.state == "idle"

    async def test_file_output_event(self):
        bus = EventBus()
        queue = bus.subscribe(FileReadyEvent)
        sm, session = _make_session(event_bus=bus)

        await sm._publish_agent_event(session, {
            "type": "file_output",
            "file_path": "/tmp/report.md",
            "file_name": "report.md",
        })

        ev = queue.get_nowait()
        assert ev.file_path == "/tmp/report.md"
        assert ev.file_name == "report.md"
