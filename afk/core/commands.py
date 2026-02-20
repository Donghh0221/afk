"""Command API — the single entry point for all control planes.

Every control plane (Telegram, CLI, Web) calls these methods.
Commands return plain dataclasses, never messenger-specific objects.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from afk.core.git_worktree import git_init, is_git_repo
from afk.core.session_manager import SessionManager, Session
from afk.storage.message_store import MessageStore
from afk.storage.project_store import ProjectStore

if TYPE_CHECKING:
    from afk.capabilities.tunnel.tunnel import TunnelCapability
    from afk.ports.stt import STTPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    name: str
    channel_id: str
    project_name: str
    state: str
    worktree_path: str
    verbose: bool


@dataclass
class SessionStatus:
    name: str
    state: str
    agent_alive: bool
    project_name: str
    project_path: str
    worktree_path: str
    tunnel_url: str | None


# ---------------------------------------------------------------------------
# Command API
# ---------------------------------------------------------------------------

class Commands:
    """Facade exposing all user-facing operations.

    Control planes parse user input and call these methods.
    Results are rendered by the control plane's event renderer.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        project_store: ProjectStore,
        message_store: MessageStore | None = None,
        stt: STTPort | None = None,
        tunnel: TunnelCapability | None = None,
        base_path: str | None = None,
    ) -> None:
        self._sm = session_manager
        self._ps = project_store
        self._ms = message_store or MessageStore()
        self._stt = stt
        self._tunnel = tunnel
        self._base_path = base_path

    @property
    def message_store(self) -> MessageStore:
        return self._ms

    @property
    def has_voice_support(self) -> bool:
        return self._stt is not None

    # -- Project commands --------------------------------------------------

    def cmd_add_project(self, name: str, path: str) -> tuple[bool, str]:
        """Register a project. Returns (success, message)."""
        try:
            ok = self._ps.add(name, path)
            if ok:
                return True, f"Project registered: {name} → {path}"
            return False, f"Project already registered: {name}"
        except ValueError as e:
            return False, str(e)

    def cmd_list_projects(self) -> dict[str, dict]:
        """List all registered projects."""
        return self._ps.list_all()

    def cmd_remove_project(self, name: str) -> tuple[bool, str]:
        """Remove a project. Returns (success, message)."""
        ok = self._ps.remove(name)
        if ok:
            return True, f"Project removed: {name}"
        return False, f"Unregistered project: {name}"

    def cmd_get_project(self, name: str) -> dict | None:
        """Look up a project by name."""
        return self._ps.get(name)

    # -- Session commands --------------------------------------------------

    async def cmd_new_session(
        self, project_name: str, verbose: bool = False,
        channel_id: str | None = None,
        agent: str | None = None,
    ) -> Session:
        """Create a new session. Raises RuntimeError on failure.

        Smart project resolution:
        1. Already registered in ProjectStore → use it.
        2. ``base_path/{name}`` exists → auto-register (git init if needed).
        3. ``base_path`` set but dir missing → mkdir + git init + register.
        4. No ``base_path`` and not registered → error.

        *channel_id* — if provided, skip messenger channel creation
        (used by the web control plane).
        *agent* — override agent runtime for this session.
        """
        project = self._ps.get(project_name)

        if not project and self._base_path:
            project_dir = Path(self._base_path) / project_name
            if project_dir.is_dir():
                # Existing directory — auto-register (git init if needed)
                if not await is_git_repo(str(project_dir)):
                    await git_init(str(project_dir))
                self._ps.add(project_name, str(project_dir))
                project = self._ps.get(project_name)
            else:
                # Create new project directory
                project_dir.mkdir(parents=True, exist_ok=True)
                await git_init(str(project_dir))
                self._ps.add(project_name, str(project_dir))
                project = self._ps.get(project_name)

        if not project:
            hint = (
                " Set AFK_BASE_PATH to auto-create projects."
                if not self._base_path else ""
            )
            raise ValueError(
                f"Unregistered project: {project_name}\n"
                f"Check /project list for available projects.{hint}"
            )

        session = await self._sm.create_session(
            project_name, project["path"],
            channel_id=channel_id, agent_name=agent,
        )
        session.verbose = verbose
        return session

    async def cmd_send_message(self, channel_id: str, text: str) -> bool:
        """Forward a text message to a session. Returns success."""
        ok = await self._sm.send_to_session(channel_id, text)
        if ok:
            self._ms.append(channel_id, "user", text)
        return ok

    async def cmd_send_voice(
        self, channel_id: str, audio_path: str
    ) -> tuple[bool, str]:
        """Transcribe and forward voice. Returns (success, transcript)."""
        if not self._stt:
            return False, "Voice support not available."

        try:
            text = await self._stt.transcribe(audio_path)
        finally:
            try:
                os.unlink(audio_path)
            except OSError:
                pass

        if not text or not text.strip():
            return False, ""

        ok = await self._sm.send_to_session(channel_id, text)
        if ok:
            self._ms.append(channel_id, "user", f"[voice] {text}")
        return ok, text

    def cmd_get_session(self, channel_id: str) -> Session | None:
        """Look up session by channel ID."""
        return self._sm.get_session(channel_id)

    def cmd_list_sessions(self) -> list[SessionInfo]:
        """List all active sessions."""
        return [
            SessionInfo(
                name=s.name,
                channel_id=s.channel_id,
                project_name=s.project_name,
                state=s.state,
                worktree_path=s.worktree_path,
                verbose=s.verbose,
            )
            for s in self._sm.list_sessions()
        ]

    async def cmd_stop_session(self, channel_id: str) -> bool:
        """Stop a session. Returns success."""
        return await self._sm.stop_session(channel_id)

    async def cmd_complete_session(self, channel_id: str) -> tuple[bool, str]:
        """Complete session (merge + cleanup). Returns (success, message)."""
        return await self._sm.complete_session(channel_id)

    def cmd_get_status(self, channel_id: str) -> SessionStatus | None:
        """Get session status."""
        session = self._sm.get_session(channel_id)
        if not session:
            return None

        tunnel_url = None
        if self._tunnel:
            t = self._tunnel.get_tunnel(channel_id)
            if t:
                tunnel_url = t.public_url

        return SessionStatus(
            name=session.name,
            state=session.state,
            agent_alive=session.agent.is_alive,
            project_name=session.project_name,
            project_path=session.project_path,
            worktree_path=session.worktree_path,
            tunnel_url=tunnel_url,
        )

    async def cmd_permission_response(
        self, channel_id: str, request_id: str, allowed: bool
    ) -> bool:
        """Forward permission response to agent. Returns success."""
        return await self._sm.send_permission_response(
            channel_id, request_id, allowed
        )

    # -- Tunnel commands ---------------------------------------------------

    async def cmd_start_tunnel(self, channel_id: str) -> str:
        """Start dev-server tunnel. Returns public URL. Raises RuntimeError."""
        if not self._tunnel:
            raise RuntimeError("Tunnel capability not available.")

        session = self._sm.get_session(channel_id)
        if not session:
            raise RuntimeError("No session found for this topic.")

        # Check if already running
        existing = self._tunnel.get_tunnel(channel_id)
        if existing:
            return existing.public_url

        return await self._tunnel.start_tunnel(channel_id, session.worktree_path)

    async def cmd_stop_tunnel(self, channel_id: str) -> bool:
        """Stop tunnel. Returns True if stopped."""
        if not self._tunnel:
            return False
        return await self._tunnel.stop_tunnel(channel_id)

    def cmd_get_tunnel_url(self, channel_id: str) -> str | None:
        """Get tunnel URL if active."""
        if not self._tunnel:
            return None
        t = self._tunnel.get_tunnel(channel_id)
        return t.public_url if t else None
