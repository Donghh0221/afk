from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from afk.core.session_manager import SessionManager, Session
from afk.dashboard.message_store import MessageStore
from afk.storage.project_store import ProjectStore

if TYPE_CHECKING:
    from afk.messenger.telegram.adapter import TelegramAdapter

logger = logging.getLogger(__name__)


class Orchestrator:
    """Routes messenger events to session/project management."""

    def __init__(
        self,
        messenger: TelegramAdapter,
        session_manager: SessionManager,
        project_store: ProjectStore,
        message_store: MessageStore | None = None,
    ) -> None:
        self._messenger = messenger
        self._sm = session_manager
        self._ps = project_store
        self._ms = message_store or MessageStore()

        # Register messenger callbacks
        messenger.set_on_text(self._handle_text)
        messenger.set_on_command("project", self._handle_project_command)
        messenger.set_on_command("new", self._handle_new_command)
        messenger.set_on_command("sessions", self._handle_sessions_command)
        messenger.set_on_command("stop", self._handle_stop_command)
        messenger.set_on_command("status", self._handle_status_command)
        messenger.set_on_permission_response(self._handle_permission_response)

        # Register Claude response callback
        session_manager.set_on_claude_message(self._handle_claude_message)

    async def _handle_text(self, channel_id: str, text: str) -> None:
        """Forward text messages to the corresponding session's Claude Code."""
        if channel_id == "general":
            return  # Only commands in General topic

        session = self._sm.get_session(channel_id)
        if not session:
            return  # Ignore topics without a session

        if not session.process.is_alive:
            await self._messenger.send_message(
                channel_id, "⚠️ Session has ended. Use /resume to restart."
            )
            return

        if session.state == "waiting_permission":
            await self._messenger.send_message(
                channel_id, "⏳ Waiting for permission approval. Please press the button above.",
                silent=True,
            )
            return

        # Forward message to Claude Code
        msg_id = await self._messenger.send_message(
            channel_id, "⏳ Forwarding task...", silent=True
        )
        ok = await self._sm.send_to_session(channel_id, text)
        if not ok:
            await self._messenger.edit_message(
                channel_id, msg_id, "❌ Failed to forward message"
            )
        else:
            self._ms.append(channel_id, "user", text)
            await self._messenger.edit_message(
                channel_id, msg_id, "📝 Task started..."
            )

    async def _handle_project_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/project add|list|remove handler."""
        if not args:
            await self._messenger.send_message(
                channel_id,
                "Usage:\n"
                "/project add <path> <name>\n"
                "/project list\n"
                "/project remove <name>",
            )
            return

        sub = args[0].lower()

        if sub == "add" and len(args) >= 3:
            path, name = args[1], args[2]
            try:
                ok = self._ps.add(name, path)
                if ok:
                    await self._messenger.send_message(
                        channel_id, f"✅ Project registered: {name} → {path}"
                    )
                else:
                    await self._messenger.send_message(
                        channel_id, f"⚠️ Project already registered: {name}"
                    )
            except ValueError as e:
                await self._messenger.send_message(channel_id, f"❌ {e}")

        elif sub == "list":
            projects = self._ps.list_all()
            if not projects:
                await self._messenger.send_message(
                    channel_id, "No registered projects."
                )
            else:
                lines = [f"📁 {name}: {info['path']}" for name, info in projects.items()]
                await self._messenger.send_message(channel_id, "\n".join(lines))

        elif sub == "remove" and len(args) >= 2:
            name = args[1]
            ok = self._ps.remove(name)
            if ok:
                await self._messenger.send_message(
                    channel_id, f"✅ Project removed: {name}"
                )
            else:
                await self._messenger.send_message(
                    channel_id, f"⚠️ Unregistered project: {name}"
                )
        else:
            await self._messenger.send_message(
                channel_id, "❌ Invalid command. Use /project to see usage."
            )

    async def _handle_new_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/new <project_name> — create a new session."""
        if not args:
            await self._messenger.send_message(
                channel_id, "Usage: /new <project_name>"
            )
            return

        project_name = args[0]
        project = self._ps.get(project_name)
        if not project:
            await self._messenger.send_message(
                channel_id,
                f"❌ Unregistered project: {project_name}\n"
                "Check /project list for available projects.",
            )
            return

        await self._messenger.send_message(
            channel_id, f"⏳ Creating session: {project_name}...", silent=True
        )

        try:
            session = await self._sm.create_session(
                project_name, project["path"]
            )
            await self._messenger.send_message(
                channel_id,
                f"✅ Session created: {session.name}\n"
                f"Send messages in the topic to talk to Claude Code.",
            )
            await self._messenger.send_message(
                session.channel_id,
                f"🚀 Session started: {session.name}\n"
                f"📁 Project: {project_name} ({project['path']})\n\n"
                f"Messages will be forwarded to Claude Code.",
            )
        except Exception as e:
            logger.exception("Failed to create session")
            await self._messenger.send_message(
                channel_id, f"❌ Failed to create session: {e}"
            )

    async def _handle_sessions_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/sessions — list all active sessions."""
        sessions = self._sm.list_sessions()
        if not sessions:
            await self._messenger.send_message(
                channel_id, "No active sessions."
            )
            return

        lines = []
        for s in sessions:
            status_emoji = {
                "idle": "💤",
                "running": "🏃",
                "waiting_permission": "⏳",
                "stopped": "🔴",
            }.get(s.state, "❓")
            lines.append(f"{status_emoji} {s.name} [{s.state}]")

        await self._messenger.send_message(channel_id, "\n".join(lines))

    async def _handle_stop_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/stop — stop the current topic's session."""
        session = self._sm.get_session(channel_id)
        if not session:
            await self._messenger.send_message(
                channel_id, "⚠️ No session linked to this topic."
            )
            return

        await self._sm.stop_session(channel_id)
        await self._messenger.send_message(
            channel_id, f"🔴 Session stopped: {session.name}"
        )

    async def _handle_status_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/status — query current session status."""
        session = self._sm.get_session(channel_id)
        if not session:
            await self._messenger.send_message(
                channel_id, "⚠️ No session linked to this topic."
            )
            return

        alive = "✅ Running" if session.process.is_alive else "🔴 Stopped"
        await self._messenger.send_message(
            channel_id,
            f"📊 Session: {session.name}\n"
            f"State: {session.state}\n"
            f"Process: {alive}\n"
            f"Project: {session.project_name} ({session.project_path})",
        )

    async def _handle_permission_response(
        self, channel_id: str, request_id: str, choice: str
    ) -> None:
        """Handle permission button response."""
        allowed = choice == "allow"
        await self._sm.send_permission_response(channel_id, request_id, allowed)

    async def _handle_claude_message(
        self, session: Session, msg: dict
    ) -> None:
        """Forward Claude Code stdout messages to messenger."""
        msg_type = msg.get("type")

        try:
            if msg_type == "system":
                sid = msg.get("session_id")
                if sid:
                    session.claude_session_id = sid
                session.state = "idle"
                self._ms.append(
                    session.channel_id, "system",
                    f"Session ready (id={sid})" if sid else "System message",
                )

            elif msg_type == "assistant":
                session.state = "running"
                await self._handle_assistant_message(session, msg)

            elif msg_type == "result":
                cost_usd = msg.get("total_cost_usd", 0)
                duration_ms = msg.get("duration_ms", 0)
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
                self._ms.append(
                    session.channel_id, "result", f"Done{info}", meta=meta,
                )

                session.state = "idle"
                await self._messenger.send_message(
                    session.channel_id,
                    f"✅ Done{info}",
                )

        except Exception:
            logger.exception(
                "Error handling Claude message (type=%s) for %s",
                msg_type, session.name,
            )

    async def _handle_assistant_message(
        self, session: Session, msg: dict
    ) -> None:
        """Process assistant messages — display text and tool use separately."""
        # stream-json: content can be at top level or nested under message
        content_blocks = msg.get("content") or msg.get("message", {}).get("content", [])

        if isinstance(content_blocks, str):
            if content_blocks:
                self._ms.append(session.channel_id, "assistant", content_blocks)
                await self._messenger.send_message(
                    session.channel_id, content_blocks, silent=True
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
                args_str = self._summarize_tool_args(tool_input)
                tool_lines.append(f"🔧 {tool_name}: {args_str}")

            elif block_type == "tool_result":
                result_text = self._summarize_tool_result(block)
                if result_text:
                    result_lines.append(result_text)

        if texts:
            text_body = "\n".join(texts)
            self._ms.append(session.channel_id, "assistant", text_body)
            await self._messenger.send_message(
                session.channel_id, text_body, silent=True
            )
        if tool_lines:
            tool_body = "\n".join(tool_lines)
            self._ms.append(session.channel_id, "tool", tool_body)
            await self._messenger.send_message(
                session.channel_id, tool_body, silent=True
            )
        if result_lines:
            result_body = "\n".join(result_lines)
            self._ms.append(session.channel_id, "tool", result_body)
            await self._messenger.send_message(
                session.channel_id, result_body, silent=True
            )

    @staticmethod
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

    @staticmethod
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
            # Extract text from content block list
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

        # Truncate long results
        if len(text) > 500:
            text = text[:500] + "…"

        return f"{prefix}: {text}"
