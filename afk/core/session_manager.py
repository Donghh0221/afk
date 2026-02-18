from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from afk.core.claude_process import ClaudeProcess

if TYPE_CHECKING:
    from afk.messenger.telegram.adapter import TelegramAdapter

logger = logging.getLogger(__name__)


@dataclass
class Session:
    name: str
    project_name: str
    project_path: str
    channel_id: str
    process: ClaudeProcess
    claude_session_id: str | None = None
    state: str = "idle"  # idle | running | waiting_permission | stopped
    created_at: float = field(default_factory=time.time)
    _response_task: asyncio.Task | None = field(default=None, repr=False)


class SessionManager:
    """Session pool management. Create/stop/restore/query sessions."""

    def __init__(
        self,
        messenger: TelegramAdapter,
        data_dir: Path,
    ) -> None:
        self._messenger = messenger
        self._sessions: dict[str, Session] = {}  # channel_id -> Session
        self._session_counter: dict[str, int] = {}  # project_name -> count
        self._data_dir = data_dir
        self._on_claude_message: asyncio.Callable | None = None

    def set_on_claude_message(self, callback) -> None:
        """Register Claude response callback: (session, message_dict) -> None"""
        self._on_claude_message = callback

    async def create_session(
        self, project_name: str, project_path: str
    ) -> Session:
        """Create new session: forum topic + Claude Code process."""
        # Assign session number
        count = self._session_counter.get(project_name, 0) + 1
        self._session_counter[project_name] = count
        session_name = f"{project_name}-session-{count}"

        # Create forum topic
        channel_id = await self._messenger.create_session_channel(session_name)

        # Start Claude Code process
        process = ClaudeProcess()
        await process.start(project_path)

        session = Session(
            name=session_name,
            project_name=project_name,
            project_path=project_path,
            channel_id=channel_id,
            process=process,
        )

        self._sessions[channel_id] = session

        # Start response reading task
        session._response_task = asyncio.create_task(
            self._read_loop(session)
        )

        logger.info("Created session: %s (channel=%s)", session_name, channel_id)
        return session

    async def stop_session(self, channel_id: str) -> bool:
        """Stop a session."""
        session = self._sessions.get(channel_id)
        if not session:
            return False

        session.state = "stopped"
        if session._response_task:
            session._response_task.cancel()
        await session.process.stop()
        # Save session ID (for resume later)
        session.claude_session_id = session.process.session_id
        self._save_sessions()
        del self._sessions[channel_id]
        logger.info("Stopped session: %s", session.name)
        return True

    def get_session(self, channel_id: str) -> Session | None:
        """Look up session by channel ID."""
        return self._sessions.get(channel_id)

    def list_sessions(self) -> list[Session]:
        """List all active sessions."""
        return list(self._sessions.values())

    async def send_to_session(self, channel_id: str, text: str) -> bool:
        """Forward a message to a session."""
        session = self._sessions.get(channel_id)
        if not session or not session.process.is_alive:
            return False

        session.state = "running"
        await session.process.send_message(text)
        return True

    async def send_permission_response(
        self, channel_id: str, request_id: str, allowed: bool
    ) -> bool:
        """Forward permission response to a session."""
        session = self._sessions.get(channel_id)
        if not session or not session.process.is_alive:
            return False

        await session.process.send_permission_response(request_id, allowed)
        session.state = "running"
        return True

    async def _read_loop(self, session: Session) -> None:
        """Claude Code stdout read loop."""
        try:
            async for msg in session.process.read_responses():
                if self._on_claude_message:
                    try:
                        await self._on_claude_message(session, msg)
                    except Exception:
                        logger.exception(
                            "Error handling message for %s: %s",
                            session.name, msg.get("type"),
                        )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Read loop crashed for %s", session.name)
        finally:
            if session.state != "stopped":
                session.state = "stopped"
                logger.info("Session ended: %s", session.name)
                try:
                    await self._messenger.send_message(
                        session.channel_id,
                        f"⚠️ Session ended: {session.name}\n"
                        f"Use /new in General to start a new session.",
                    )
                except Exception:
                    pass

    def _save_sessions(self) -> None:
        """Save session data for recovery."""
        data = {}
        for cid, s in self._sessions.items():
            data[cid] = {
                "name": s.name,
                "project_name": s.project_name,
                "project_path": s.project_path,
                "channel_id": s.channel_id,
                "claude_session_id": s.process.session_id,
                "state": s.state,
            }
        path = self._data_dir / "sessions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
