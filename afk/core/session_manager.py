from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from afk.core.claude_process import ClaudeProcess
from afk.core.git_worktree import (
    commit_worktree_changes,
    create_worktree,
    is_git_repo,
    list_afk_worktrees,
    merge_branch_to_main,
    remove_worktree,
    remove_worktree_after_merge,
)

if TYPE_CHECKING:
    from afk.messenger.telegram.adapter import TelegramAdapter
    from afk.storage.project_store import ProjectStore

logger = logging.getLogger(__name__)


@dataclass
class Session:
    name: str
    project_name: str
    project_path: str  # main repo path (never changes)
    worktree_path: str  # isolated worktree directory for this session
    channel_id: str
    process: ClaudeProcess
    claude_session_id: str | None = None
    state: str = "idle"  # idle | running | waiting_permission | stopped
    verbose: bool = False
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
        """Create new session: git worktree + forum topic + Claude Code process."""
        # Validate git repository
        if not await is_git_repo(project_path):
            raise RuntimeError(
                f"Project '{project_name}' at {project_path} is not a git repository. "
                "Git worktree isolation requires a git repo."
            )

        # Assign session number
        count = self._session_counter.get(project_name, 0) + 1
        self._session_counter[project_name] = count
        session_name = f"{project_name}-session-{count}"

        # Compute worktree path and branch name
        worktree_root = Path(project_path) / ".afk-worktrees"
        worktree_path = str(worktree_root / session_name)
        branch_name = f"afk/{session_name}"

        # Clean up stale worktree if path already exists
        if Path(worktree_path).exists():
            logger.warning(
                "Worktree path already exists, attempting cleanup: %s",
                worktree_path,
            )
            await remove_worktree(project_path, worktree_path, branch_name)
            if Path(worktree_path).exists():
                shutil.rmtree(worktree_path, ignore_errors=True)

        # Create git worktree (raises RuntimeError on failure)
        await create_worktree(project_path, worktree_path, branch_name)

        # Create forum topic (only after git succeeds — no orphan topics)
        channel_id = await self._messenger.create_session_channel(session_name)

        # Start Claude Code process in the worktree directory
        process = ClaudeProcess()
        await process.start(worktree_path)

        session = Session(
            name=session_name,
            project_name=project_name,
            project_path=project_path,
            worktree_path=worktree_path,
            channel_id=channel_id,
            process=process,
        )

        self._sessions[channel_id] = session

        # Start response reading task
        session._response_task = asyncio.create_task(
            self._read_loop(session)
        )

        logger.info(
            "Created session: %s (channel=%s, worktree=%s)",
            session_name, channel_id, worktree_path,
        )
        return session

    async def stop_session(self, channel_id: str) -> bool:
        """Stop a session and clean up its git worktree."""
        session = self._sessions.get(channel_id)
        if not session:
            return False

        session.state = "stopped"
        if session._response_task:
            session._response_task.cancel()

        # Stop Claude process first (releases file handles on worktree)
        await session.process.stop()
        session.claude_session_id = session.process.session_id

        # Remove git worktree and branch (best-effort)
        branch_name = f"afk/{session.name}"
        await remove_worktree(
            session.project_path, session.worktree_path, branch_name
        )

        self._save_sessions()
        del self._sessions[channel_id]
        # Delete the forum topic
        try:
            await self._messenger.close_session_channel(channel_id)
        except Exception:
            logger.warning("Failed to delete topic for %s", session.name)
        logger.info("Stopped session: %s", session.name)
        return True

    async def complete_session(self, channel_id: str) -> tuple[bool, str]:
        """Complete a session: merge branch into main, then clean up.

        On merge failure the session is left intact so the user can resolve.
        """
        session = self._sessions.get(channel_id)
        if not session:
            return False, "No session found for this topic."

        # 1. Stop Claude process (releases file handles on worktree)
        session.state = "stopped"
        if session._response_task:
            session._response_task.cancel()
        await session.process.stop()
        session.claude_session_id = session.process.session_id

        # 2. Commit any uncommitted changes in the worktree
        await commit_worktree_changes(session.worktree_path, session.name)

        # 3. Merge branch into main
        branch_name = f"afk/{session.name}"
        success, merge_output = await merge_branch_to_main(
            session.project_path, branch_name
        )

        if not success:
            # Restart Claude so session stays usable
            session.state = "idle"
            session.process = ClaudeProcess()
            await session.process.start(
                session.worktree_path, session.claude_session_id
            )
            session._response_task = asyncio.create_task(
                self._read_loop(session)
            )
            return False, (
                f"Merge failed for branch '{branch_name}'.\n"
                f"Error: {merge_output}\n\n"
                f"Session remains active. Resolve conflicts and try again, "
                f"or use /stop to discard changes."
            )

        # 4. Remove worktree and branch
        await remove_worktree_after_merge(
            session.project_path, session.worktree_path, branch_name
        )

        # 5. Clean up session state
        self._save_sessions()
        del self._sessions[channel_id]

        # 6. Delete the forum topic
        try:
            await self._messenger.close_session_channel(channel_id)
        except Exception:
            logger.warning("Failed to delete topic for %s", session.name)

        logger.info("Completed session: %s (merged into main)", session.name)
        return True, f"Session '{session.name}' completed. Branch merged into main."

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
                try:
                    await self._messenger.close_session_channel(
                        session.channel_id
                    )
                except Exception:
                    logger.warning(
                        "Failed to delete topic for %s", session.name
                    )

    async def cleanup_orphan_worktrees(
        self, project_store: ProjectStore
    ) -> None:
        """Remove afk/ worktrees left behind by a previous crash."""
        for project_name, info in project_store.list_all().items():
            project_path = info["path"]
            try:
                orphans = await list_afk_worktrees(project_path)
            except Exception:
                logger.exception(
                    "Failed to list worktrees for %s", project_name
                )
                continue

            for wt in orphans:
                logger.warning(
                    "Orphan worktree detected: %s (branch=%s) — removing",
                    wt["path"],
                    wt["branch"],
                )
                await remove_worktree(project_path, wt["path"], wt["branch"])

    def _save_sessions(self) -> None:
        """Save session data for recovery."""
        data = {}
        for cid, s in self._sessions.items():
            data[cid] = {
                "name": s.name,
                "project_name": s.project_name,
                "project_path": s.project_path,
                "worktree_path": s.worktree_path,
                "channel_id": s.channel_id,
                "claude_session_id": s.process.session_id,
                "state": s.state,
            }
        path = self._data_dir / "sessions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
