from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Awaitable

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
from afk.core.session_log import SessionLogger
from afk.core.git_worktree import (
    CommitMessageFn,
    commit_worktree_changes,
    create_worktree,
    delete_branch,
    is_git_repo,
    list_afk_worktrees,
    merge_branch_to_main,
    remove_worktree,
)

if TYPE_CHECKING:
    from afk.ports.agent import AgentPort
    from afk.ports.control_plane import ControlPlanePort
    from afk.storage.project_store import ProjectStore
    from afk.storage.template_store import TemplateConfig

logger = logging.getLogger(__name__)


@dataclass
class Session:
    name: str
    project_name: str
    project_path: str  # main repo path (never changes)
    worktree_path: str  # isolated worktree directory for this session
    channel_id: str
    agent: AgentPort
    agent_session_id: str | None = None
    state: str = "idle"  # idle | running | waiting_permission | stopped
    verbose: bool = False
    managed_channel: bool = True  # False for web channels (no Telegram topic)
    template_name: str | None = None
    created_at: float = field(default_factory=time.time)
    _response_task: asyncio.Task | None = field(default=None, repr=False)
    _session_logger: SessionLogger | None = field(default=None, repr=False)


# Callback type for session cleanup (capabilities register these)
SessionCleanupFn = Callable[[str], Awaitable[None]]


class SessionManager:
    """Session pool management. Create/stop/restore/query sessions."""

    def __init__(
        self,
        messenger: ControlPlanePort,
        data_dir: Path,
        event_bus: EventBus | None = None,
        agent_factory: Callable[[], AgentPort] | None = None,
        commit_message_fn: CommitMessageFn | None = None,
        agent_registry: dict[str, Callable[[], AgentPort]] | None = None,
        default_agent: str = "claude",
    ) -> None:
        self._messenger = messenger
        self._sessions: dict[str, Session] = {}  # channel_id -> Session
        self._data_dir = data_dir
        self._event_bus = event_bus or EventBus()
        self._agent_factory = agent_factory
        self._commit_message_fn = commit_message_fn
        self._cleanup_callbacks: list[SessionCleanupFn] = []
        self._agent_registry = agent_registry or {}
        self._default_agent = default_agent

    def add_cleanup_callback(self, callback: SessionCleanupFn) -> None:
        """Register a cleanup callback called when a session stops/completes."""
        self._cleanup_callbacks.append(callback)

    async def _run_cleanup(self, channel_id: str) -> None:
        """Run all registered cleanup callbacks for a session."""
        for cb in self._cleanup_callbacks:
            try:
                await cb(channel_id)
            except Exception:
                logger.exception("Cleanup callback failed for %s", channel_id)

    def _create_agent(self, agent_name: str | None = None) -> AgentPort:
        """Create a new agent instance.

        Looks up *agent_name* in the registry first, then falls back to the
        default agent name, then to the legacy ``agent_factory``.
        """
        name = agent_name or self._default_agent
        if name in self._agent_registry:
            return self._agent_registry[name]()
        if self._agent_factory is not None:
            return self._agent_factory()
        raise RuntimeError(f"No agent factory for '{name}'")

    async def create_session(
        self, project_name: str, project_path: str,
        channel_id: str | None = None,
        agent_name: str | None = None,
        template: TemplateConfig | None = None,
    ) -> Session:
        """Create new session: git worktree + forum topic + agent process.

        If *channel_id* is provided the messenger channel creation is skipped
        (used by the web control plane which manages its own channel IDs).
        """
        # Validate git repository
        if not await is_git_repo(project_path):
            raise RuntimeError(
                f"Project '{project_name}' at {project_path} is not a git repository. "
                "Git worktree isolation requires a git repo."
            )

        # Assign timestamp-based session name (YYMMDD-HHMMSS)
        ts = datetime.now(timezone.utc).strftime("%y%m%d-%H%M%S")
        session_name = f"{project_name.lower()}-{ts}"

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

        # Apply template scaffold files (before agent starts)
        if template:
            from afk.storage.template_store import TemplateStore
            TemplateStore.apply(template, worktree_path)

        # Per-session logging (stored in data_dir, survives worktree cleanup)
        session_logger = SessionLogger(
            self._data_dir / "logs" / session_name, session_name,
        )
        session_logger.start()
        session_logger.logger.info(
            "Session created: project=%s worktree=%s", project_name, worktree_path,
        )

        # Create channel — skip messenger call when a pre-assigned channel_id
        # is provided (web control plane supplies its own IDs).
        managed_channel = channel_id is None
        if managed_channel:
            channel_id = await self._messenger.create_session_channel(session_name)

        # Start agent process in the worktree directory
        agent = self._create_agent(agent_name)
        await agent.start(worktree_path, stderr_log_path=session_logger.stderr_log_path)

        session = Session(
            name=session_name,
            project_name=project_name,
            project_path=project_path,
            worktree_path=worktree_path,
            channel_id=channel_id,
            agent=agent,
            managed_channel=managed_channel,
            template_name=template.name if template else None,
            _session_logger=session_logger,
        )

        self._sessions[channel_id] = session
        self._save_sessions()

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

        # Run capability cleanup (tunnel, etc.)
        await self._run_cleanup(channel_id)

        # Stop agent process first (releases file handles on worktree)
        await session.agent.stop()
        session.agent_session_id = session.agent.session_id

        # Close per-session logger
        if session._session_logger:
            session._session_logger.logger.info("Session stopped by user")
            session._session_logger.close()

        # Remove git worktree and branch (best-effort)
        branch_name = f"afk/{session.name}"
        await remove_worktree(
            session.project_path, session.worktree_path, branch_name
        )

        del self._sessions[channel_id]
        self._save_sessions()
        # Delete the forum topic (only for messenger-managed channels)
        if session.managed_channel:
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

        # 1. Run capability cleanup (tunnel, etc.)
        await self._run_cleanup(channel_id)

        # 2. Stop agent process (releases file handles on worktree)
        session.state = "stopped"
        if session._response_task:
            session._response_task.cancel()
        await session.agent.stop()
        session.agent_session_id = session.agent.session_id

        # 3. Commit any uncommitted changes in the worktree
        await commit_worktree_changes(
            session.worktree_path, session.name,
            commit_message_fn=self._commit_message_fn,
        )

        # 4. Merge branch into main (rebase in worktree, remove worktree, ff-merge)
        branch_name = f"afk/{session.name}"
        success, merge_output = await merge_branch_to_main(
            session.project_path, branch_name, session.worktree_path
        )

        if not success:
            # Restart agent so session stays usable
            session.state = "idle"
            session.agent = self._create_agent()
            stderr_path = session._session_logger.stderr_log_path if session._session_logger else None
            await session.agent.start(
                session.worktree_path, session.agent_session_id,
                stderr_log_path=stderr_path,
            )
            session._response_task = asyncio.create_task(
                self._read_loop(session)
            )
            if session._session_logger:
                session._session_logger.logger.warning(
                    "Merge failed, session restarted: %s", merge_output[:200],
                )
            return False, (
                f"Merge failed for branch '{branch_name}'.\n"
                f"Error: {merge_output}\n\n"
                f"Session remains active. Resolve conflicts and try again, "
                f"or use /stop to discard changes."
            )

        # 5. Delete the branch (worktree already removed by merge_branch_to_main)
        await delete_branch(session.project_path, branch_name)

        # 6. Close per-session logger
        if session._session_logger:
            session._session_logger.logger.info("Session completed (merged into main)")
            session._session_logger.close()

        # 7. Clean up session state
        del self._sessions[channel_id]
        self._save_sessions()

        # 8. Delete the forum topic (only for messenger-managed channels)
        if session.managed_channel:
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
        if not session or not session.agent.is_alive:
            return False

        session.state = "running"
        await session.agent.send_message(text)
        return True

    async def send_permission_response(
        self, channel_id: str, request_id: str, allowed: bool
    ) -> bool:
        """Forward permission response to a session."""
        session = self._sessions.get(channel_id)
        if not session or not session.agent.is_alive:
            return False

        await session.agent.send_permission_response(request_id, allowed)
        session.state = "running"
        return True

    async def _read_loop(self, session: Session) -> None:
        """Agent stdout read loop — publishes events to EventBus."""
        try:
            async for msg in session.agent.read_responses():
                try:
                    if session._session_logger:
                        session._session_logger.write_raw(
                            json.dumps(msg, ensure_ascii=False) + "\n"
                        )
                    self._publish_agent_event(session, msg)
                except Exception:
                    logger.exception(
                        "Error publishing event for %s: %s",
                        session.name, msg.get("type"),
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Read loop crashed for %s", session.name)
        finally:
            if session.state != "stopped":
                session.state = "stopped"
                await self._run_cleanup(session.channel_id)
                if session._session_logger:
                    session._session_logger.logger.info("Session ended (read loop exit)")
                logger.info("Session ended: %s", session.name)
                self._event_bus.publish(AgentStoppedEvent(
                    channel_id=session.channel_id,
                    session_name=session.name,
                ))

    @staticmethod
    def _classify_assistant_level(content_blocks: list) -> EventLevel:
        """Classify the semantic level of assistant content blocks.

        Pure content-based classification — no presentation concerns.
        """
        if isinstance(content_blocks, str):
            return EventLevel.INFO
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                return EventLevel.INFO
        return EventLevel.PROGRESS

    def _publish_agent_event(self, session: Session, msg: dict) -> None:
        """Convert raw agent message to typed event and publish."""
        msg_type = msg.get("type")

        if msg_type == "system":
            sid = msg.get("session_id")
            if sid:
                session.agent_session_id = sid
                self._save_sessions()
            session.state = "idle"
            if session._session_logger:
                session._session_logger.logger.info("Agent ready: session_id=%s", sid)
            self._event_bus.publish(AgentSystemEvent(
                channel_id=session.channel_id,
                agent_session_id=sid,
            ))

        elif msg_type == "assistant":
            session.state = "running"
            content_blocks = (
                msg.get("content")
                or msg.get("message", {}).get("content", [])
            )
            level = self._classify_assistant_level(content_blocks)
            self._event_bus.publish(AgentAssistantEvent(
                channel_id=session.channel_id,
                content_blocks=content_blocks,
                session_name=session.name,
                level=level,
                verbose=session.verbose,
            ))

        elif msg_type == "permission_request":
            session.state = "waiting_permission"
            tool_name = msg.get("tool_name", "unknown")
            if session._session_logger:
                session._session_logger.logger.info(
                    "Permission request: tool=%s id=%s",
                    tool_name, msg.get("id", ""),
                )
            self._event_bus.publish(AgentPermissionRequestEvent(
                channel_id=session.channel_id,
                request_id=msg.get("id", ""),
                tool_name=tool_name,
                tool_input=msg.get("tool_input", {}),
            ))

        elif msg_type == "result":
            session.state = "idle"
            if session._session_logger:
                session._session_logger.logger.info(
                    "Task complete: cost=$%.4f duration=%dms",
                    msg.get("total_cost_usd", 0), msg.get("duration_ms", 0),
                )
            self._event_bus.publish(AgentResultEvent(
                channel_id=session.channel_id,
                cost_usd=msg.get("total_cost_usd", 0),
                duration_ms=msg.get("duration_ms", 0),
            ))
            self._event_bus.publish(AgentInputRequestEvent(
                channel_id=session.channel_id,
                session_name=session.name,
            ))

    async def suspend_all_sessions(self) -> None:
        """Gracefully suspend all sessions for daemon restart recovery.

        Stops agent processes but preserves worktrees and forum topics
        so sessions can be recovered on next startup.
        """
        for channel_id, session in self._sessions.items():
            if session._response_task:
                session._response_task.cancel()

            session.agent_session_id = session.agent.session_id or session.agent_session_id

            await self._run_cleanup(channel_id)
            await session.agent.stop()

            if session._session_logger:
                session._session_logger.logger.info("Session suspended for daemon restart")
                session._session_logger.close()

            session.state = "suspended"

        self._save_sessions()
        logger.info("Suspended %d sessions for recovery", len(self._sessions))

    async def recover_sessions(self, project_store: ProjectStore) -> list[Session]:
        """Load sessions from sessions.json and resume agent processes.

        Returns list of successfully recovered sessions.
        Must be called before cleanup_orphan_worktrees().
        """
        path = self._data_dir / "sessions.json"
        if not path.exists():
            return []

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read sessions.json: %s", e)
            return []

        recovered: list[Session] = []

        for channel_id, info in data.items():
            session_name = info.get("name", "unknown")
            worktree_path = info.get("worktree_path", "")
            project_name = info.get("project_name", "")
            project_path = info.get("project_path", "")
            agent_session_id = info.get("agent_session_id")
            verbose = info.get("verbose", False)
            managed_channel = info.get("managed_channel", True)
            template_name = info.get("template_name")
            created_at = info.get("created_at", time.time())

            if not Path(worktree_path).is_dir():
                logger.warning(
                    "Skip recovery for %s: worktree missing (%s)",
                    session_name, worktree_path,
                )
                continue

            if not project_store.get(project_name):
                logger.warning(
                    "Skip recovery for %s: project '%s' not registered",
                    session_name, project_name,
                )
                continue

            if not agent_session_id:
                logger.warning(
                    "Skip recovery for %s: no agent_session_id",
                    session_name,
                )
                continue

            try:
                # Reopen per-session logging (append mode preserves previous logs)
                session_logger = SessionLogger(
                    self._data_dir / "logs" / session_name, session_name,
                )
                session_logger.start()
                session_logger.logger.info("Session recovered from previous run")

                agent = self._create_agent()
                await agent.start(
                    worktree_path, agent_session_id,
                    stderr_log_path=session_logger.stderr_log_path,
                )

                session = Session(
                    name=session_name,
                    project_name=project_name,
                    project_path=project_path,
                    worktree_path=worktree_path,
                    channel_id=channel_id,
                    agent=agent,
                    agent_session_id=agent_session_id,
                    verbose=verbose,
                    managed_channel=managed_channel,
                    template_name=template_name,
                    created_at=created_at,
                    _session_logger=session_logger,
                )

                self._sessions[channel_id] = session

                session._response_task = asyncio.create_task(
                    self._read_loop(session)
                )

                recovered.append(session)
                logger.info(
                    "Recovered session: %s (channel=%s)",
                    session_name, channel_id,
                )
            except Exception:
                logger.exception("Failed to recover session %s", session_name)

        self._save_sessions()

        if recovered:
            logger.info("Recovered %d sessions from previous run", len(recovered))

        return recovered

    async def cleanup_orphan_worktrees(
        self, project_store: ProjectStore
    ) -> None:
        """Remove afk/ worktrees left behind by a previous crash.

        Skips worktrees belonging to active (recovered) sessions.
        """
        active_worktree_paths = {
            s.worktree_path for s in self._sessions.values()
        }

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
                if wt["path"] in active_worktree_paths:
                    logger.info(
                        "Keeping recovered worktree: %s (branch=%s)",
                        wt["path"], wt["branch"],
                    )
                    continue

                logger.warning(
                    "Orphan worktree detected: %s (branch=%s) — removing",
                    wt["path"],
                    wt["branch"],
                )
                await remove_worktree(project_path, wt["path"], wt["branch"])

    def _save_sessions(self) -> None:
        """Save session data for recovery (atomic write)."""
        data = {}
        for cid, s in self._sessions.items():
            data[cid] = {
                "name": s.name,
                "project_name": s.project_name,
                "project_path": s.project_path,
                "worktree_path": s.worktree_path,
                "channel_id": s.channel_id,
                "agent_session_id": s.agent.session_id or s.agent_session_id,
                "state": s.state,
                "verbose": s.verbose,
                "managed_channel": s.managed_channel,
                "template_name": s.template_name,
                "created_at": s.created_at,
            }
        path = self._data_dir / "sessions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp_path.rename(path)
