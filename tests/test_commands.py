"""Tests for the Commands facade."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from afk.core.commands import Commands, SessionInfo
from afk.core.session_manager import Session, SessionManager
from afk.storage.message_store import MessageStore
from afk.storage.project_store import ProjectStore
from afk.storage.template_store import TemplateConfig, TemplateStore


def _make_commands(
    data_dir: Path,
    tmp_path: Path,
    stt=None,
    template_store=None,
    base_path: str | None = None,
) -> Commands:
    """Create a Commands instance with mocked SessionManager."""
    project_store = ProjectStore(data_dir)
    message_store = MessageStore()
    session_manager = MagicMock(spec=SessionManager)
    return Commands(
        session_manager=session_manager,
        project_store=project_store,
        message_store=message_store,
        stt=stt,
        template_store=template_store,
        base_path=base_path,
    )


class TestProjectCommands:
    def test_add_project_success(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path)
        ok, msg = cmd.cmd_add_project("myproject", str(project_dir))
        assert ok is True
        assert "registered" in msg.lower()

    def test_add_project_duplicate(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "dup"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path)
        cmd.cmd_add_project("dup", str(project_dir))
        ok, msg = cmd.cmd_add_project("dup", str(project_dir))
        assert ok is False
        assert "already" in msg.lower()

    def test_add_project_error(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path)
        ok, msg = cmd.cmd_add_project("bad", "/no/such/dir")
        assert ok is False
        assert "directory" in msg.lower()

    def test_remove_project_success(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "rm"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path)
        cmd.cmd_add_project("rm", str(project_dir))
        ok, msg = cmd.cmd_remove_project("rm")
        assert ok is True

    def test_remove_project_not_found(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path)
        ok, msg = cmd.cmd_remove_project("nope")
        assert ok is False

    def test_list_projects(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "listed"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path)
        cmd.cmd_add_project("listed", str(project_dir))
        projects = cmd.cmd_list_projects()
        assert "listed" in projects

    def test_get_project(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "got"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path)
        cmd.cmd_add_project("got", str(project_dir))
        assert cmd.cmd_get_project("got") is not None
        assert cmd.cmd_get_project("nope") is None

    def test_project_info_not_found(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path)
        assert cmd.cmd_project_info("nope") is None

    def test_project_info_no_sessions(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "info_proj"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path)
        cmd.cmd_add_project("info_proj", str(project_dir))
        cmd._sm.list_sessions.return_value = []
        info = cmd.cmd_project_info("info_proj")
        assert info is not None
        assert info["name"] == "info_proj"
        assert info["path"] == str(project_dir)
        assert info["sessions"] == []

    def test_project_info_with_sessions(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "active_proj"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path)
        cmd.cmd_add_project("active_proj", str(project_dir))
        agent_mock = MagicMock()
        agent_mock.is_alive = True
        cmd._sm.list_sessions.return_value = [
            Session(
                name="active_proj-260220-120000",
                project_name="active_proj",
                project_path=str(project_dir),
                worktree_path="/w",
                channel_id="ch1",
                agent=agent_mock,
                agent_name="deep-research",
                state="running",
            ),
        ]
        info = cmd.cmd_project_info("active_proj")
        assert len(info["sessions"]) == 1
        assert info["sessions"][0]["agent_name"] == "deep-research"
        assert info["sessions"][0]["state"] == "running"


class TestListSessions:
    def test_converts_sessions_to_session_info(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path)
        agent_mock = MagicMock()
        agent_mock.is_alive = True
        agent_mock.session_id = None
        sessions = [
            Session(
                name="s1",
                project_name="proj",
                project_path="/p",
                worktree_path="/w",
                channel_id="ch1",
                agent=agent_mock,
                state="running",
                verbose=True,
            ),
        ]
        cmd._sm.list_sessions.return_value = sessions
        result = cmd.cmd_list_sessions()
        assert len(result) == 1
        assert isinstance(result[0], SessionInfo)
        assert result[0].name == "s1"
        assert result[0].verbose is True


class TestListTemplates:
    def test_no_template_store(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path, template_store=None)
        assert cmd.cmd_list_templates() == []

    def test_with_templates(self, data_dir: Path, tmp_path: Path):
        ts = MagicMock(spec=TemplateStore)
        ts.list_all.return_value = {
            "coding": TemplateConfig(
                name="coding",
                description="Coding template",
                template_dir=tmp_path,
                agent="claude",
            ),
        }
        cmd = _make_commands(data_dir, tmp_path, template_store=ts)
        templates = cmd.cmd_list_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "coding"
        assert templates[0]["agent"] == "claude"


class TestHasVoiceSupport:
    def test_with_stt(self, data_dir: Path, tmp_path: Path):
        stt_mock = MagicMock()
        cmd = _make_commands(data_dir, tmp_path, stt=stt_mock)
        assert cmd.has_voice_support is True

    def test_without_stt(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path, stt=None)
        assert cmd.has_voice_support is False


class TestNewSessionRejectsUnregistered:
    @pytest.mark.asyncio
    async def test_unregistered_project_raises(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path)
        with pytest.raises(ValueError, match="Unregistered project"):
            await cmd.cmd_new_session("nonexistent")

    @pytest.mark.asyncio
    async def test_error_hints_project_commands(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path)
        with pytest.raises(ValueError, match="/project add or /project init"):
            await cmd.cmd_new_session("nonexistent")


class TestInitProject:
    @pytest.mark.asyncio
    async def test_no_base_path(self, data_dir: Path, tmp_path: Path):
        cmd = _make_commands(data_dir, tmp_path, base_path=None)
        ok, msg = await cmd.cmd_init_project("myproject")
        assert ok is False
        assert "AFK_BASE_PATH" in msg

    @pytest.mark.asyncio
    async def test_already_registered(self, data_dir: Path, tmp_path: Path):
        project_dir = tmp_path / "existing"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path, base_path=str(tmp_path / "base"))
        cmd.cmd_add_project("existing", str(project_dir))
        ok, msg = await cmd.cmd_init_project("existing")
        assert ok is False
        assert "already registered" in msg.lower()

    @pytest.mark.asyncio
    async def test_init_existing_dir(self, data_dir: Path, tmp_path: Path):
        base = tmp_path / "base"
        base.mkdir()
        project_dir = base / "myproject"
        project_dir.mkdir()
        cmd = _make_commands(data_dir, tmp_path, base_path=str(base))
        ok, msg = await cmd.cmd_init_project("myproject")
        assert ok is True
        assert "registered" in msg.lower()
        assert cmd.cmd_get_project("myproject") is not None

    @pytest.mark.asyncio
    async def test_init_creates_new_dir(self, data_dir: Path, tmp_path: Path):
        base = tmp_path / "base"
        base.mkdir()
        cmd = _make_commands(data_dir, tmp_path, base_path=str(base))
        ok, msg = await cmd.cmd_init_project("newproject")
        assert ok is True
        assert "created" in msg.lower()
        assert (base / "newproject").is_dir()
        assert cmd.cmd_get_project("newproject") is not None
