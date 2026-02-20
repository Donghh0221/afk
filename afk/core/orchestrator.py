"""Orchestrator — thin glue wiring messenger callbacks to Commands.

The Orchestrator registers messenger callbacks and delegates to the
Command API.  Agent output rendering is handled separately by an
EventRenderer (subscribes to the EventBus).
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from afk.core.commands import Commands

if TYPE_CHECKING:
    from afk.ports.control_plane import ControlPlanePort

logger = logging.getLogger(__name__)


class Orchestrator:
    """Routes messenger events to the Commands API."""

    def __init__(
        self,
        messenger: ControlPlanePort,
        commands: Commands,
    ) -> None:
        self._messenger = messenger
        self._cmd = commands

        # Register messenger callbacks
        messenger.set_on_text(self._handle_text)
        if commands.has_voice_support:
            messenger.set_on_voice(self._handle_voice)
        messenger.set_on_command("project", self._handle_project_command)
        messenger.set_on_command("new", self._handle_new_command)
        messenger.set_on_command("sessions", self._handle_sessions_command)
        messenger.set_on_command("stop", self._handle_stop_command)
        messenger.set_on_command("complete", self._handle_complete_command)
        messenger.set_on_command("status", self._handle_status_command)
        messenger.set_on_command("tunnel", self._handle_tunnel_command)
        messenger.set_on_command("template", self._handle_template_command)
        messenger.set_on_unknown_command(self._handle_unknown_command)
        messenger.set_on_permission_response(self._handle_permission_response)

    # -- Text / Voice -------------------------------------------------------

    async def _handle_text(self, channel_id: str, text: str) -> None:
        """Forward text messages to the corresponding session's agent."""
        if channel_id == "general":
            return

        session = self._cmd.cmd_get_session(channel_id)
        if not session:
            return

        if not session.agent.is_alive:
            await self._messenger.send_message(
                channel_id, "⚠️ Session has ended. Use /new to start a new session."
            )
            return

        if session.state == "waiting_permission":
            await self._messenger.send_message(
                channel_id, "⏳ Waiting for permission approval. Please press the button above.",
                silent=True,
            )
            return

        msg_id = await self._messenger.send_message(
            channel_id, "⏳ Forwarding task...", silent=True
        )
        ok = await self._cmd.cmd_send_message(channel_id, text)
        if not ok:
            await self._messenger.edit_message(
                channel_id, msg_id, "❌ Failed to forward message"
            )
        else:
            await self._messenger.edit_message(
                channel_id, msg_id, "📝 Task started..."
            )

    async def _handle_voice(self, channel_id: str, file_id: str) -> None:
        """Handle voice messages: download -> transcribe -> forward."""
        if channel_id == "general":
            return

        session = self._cmd.cmd_get_session(channel_id)
        if not session:
            return

        if not session.agent.is_alive:
            await self._messenger.send_message(
                channel_id, "Session has ended. Use /new to start a new session."
            )
            return

        if session.state == "waiting_permission":
            await self._messenger.send_message(
                channel_id,
                "Waiting for permission approval. Please press the button above.",
                silent=True,
            )
            return

        msg_id = await self._messenger.send_message(
            channel_id, "Transcribing voice message...", silent=True
        )

        try:
            audio_path = await self._messenger.download_voice(file_id)
            ok, text = await self._cmd.cmd_send_voice(channel_id, audio_path)

            if not ok:
                if not text:
                    await self._messenger.edit_message(
                        channel_id, msg_id, "Could not transcribe voice message."
                    )
                else:
                    await self._messenger.edit_message(
                        channel_id, msg_id, f"Voice failed: {text}"
                    )
                return

            await self._messenger.edit_message(
                channel_id, msg_id, f"Voice: {text}"
            )
        except Exception as e:
            logger.exception("Voice transcription failed for channel %s", channel_id)
            await self._messenger.edit_message(
                channel_id, msg_id, f"Voice transcription failed: {e}"
            )

    # -- Commands -----------------------------------------------------------

    async def _handle_project_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/project add|list|remove|init handler."""
        if not args:
            await self._messenger.send_message(
                channel_id,
                "Usage:\n"
                "/project add <path> <name>\n"
                "/project list\n"
                "/project remove <name>\n"
                "/project init <name>",
            )
            return

        sub = args[0].lower()

        if sub == "add" and len(args) >= 3:
            path, name = args[1], args[2]
            ok, msg = self._cmd.cmd_add_project(name, path)
            emoji = "✅" if ok else "⚠️"
            await self._messenger.send_message(channel_id, f"{emoji} {msg}")

        elif sub == "list":
            projects = self._cmd.cmd_list_projects()
            if not projects:
                await self._messenger.send_message(
                    channel_id, "No registered projects."
                )
            else:
                lines = [f"📁 {name}: {info['path']}" for name, info in projects.items()]
                await self._messenger.send_message(channel_id, "\n".join(lines))

        elif sub == "remove" and len(args) >= 2:
            name = args[1]
            ok, msg = self._cmd.cmd_remove_project(name)
            emoji = "✅" if ok else "⚠️"
            await self._messenger.send_message(channel_id, f"{emoji} {msg}")

        elif sub == "init" and len(args) >= 2:
            name = args[1]
            ok, msg = await self._cmd.cmd_init_project(name)
            emoji = "✅" if ok else "⚠️"
            await self._messenger.send_message(channel_id, f"{emoji} {msg}")

        else:
            await self._messenger.send_message(
                channel_id, "❌ Invalid command. Use /project to see usage."
            )

    async def _handle_new_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/new <project_name> [-v|--verbose] [--agent <name>] [--template <name>]"""
        usage = "Usage: /new <project_name> [-v|--verbose] [--agent <name>] [--template <name>]"
        if not args:
            await self._messenger.send_message(channel_id, usage)
            return

        # Normalize Unicode dashes to ASCII hyphens
        # (Telegram/mobile keyboards often auto-convert -- to em-dash)
        args = [a.replace("\u2014", "--").replace("\u2013", "-") for a in args]

        verbose = "--verbose" in args or "-v" in args

        # Extract --agent / -a and --template / -t values
        agent: str | None = None
        template: str | None = None
        filtered_args: list[str] = []
        it = iter(args)
        for a in it:
            if a in ("--agent", "-a"):
                agent = next(it, None)
            elif a in ("--template", "-t"):
                template = next(it, None)
            else:
                filtered_args.append(a)

        positional = [a for a in filtered_args if not a.startswith("-")]
        if not positional:
            await self._messenger.send_message(channel_id, usage)
            return

        project_name = positional[0]

        await self._messenger.send_message(
            channel_id, f"⏳ Creating session: {project_name}...", silent=True
        )

        try:
            session = await self._cmd.cmd_new_session(
                project_name, verbose=verbose, agent=agent,
                template=template,
            )
            verbose_label = " (verbose)" if verbose else ""
            agent_label = session.agent_name
            topic_link = self._messenger.get_channel_link(session.channel_id)
            await self._messenger.send_message(
                channel_id,
                f"✅ Session created: {session.name}{verbose_label}\n"
                f"Send messages in the topic to talk to {agent_label}.",
                link_url=topic_link,
                link_label=f"Open {session.name}",
            )

            project = self._cmd.cmd_get_project(project_name)
            project_path = project["path"] if project else "unknown"
            await self._messenger.send_message(
                session.channel_id,
                f"🚀 Session started: {session.name}{verbose_label}\n"
                f"📁 Project: {project_name} ({project_path})\n"
                f"🌿 Branch: afk/{session.name}\n"
                f"📂 Worktree: {session.worktree_path}\n"
                f"🤖 Agent: {agent_label}\n\n"
                f"Messages will be forwarded to {agent_label}.",
            )
        except (ValueError, RuntimeError) as e:
            await self._messenger.send_message(
                channel_id, f"❌ {e}"
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
        sessions = self._cmd.cmd_list_sessions()
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
                "suspended": "💾",
            }.get(s.state, "❓")
            lines.append(f"{status_emoji} {s.name} [{s.state}]")

        await self._messenger.send_message(channel_id, "\n".join(lines))

    async def _handle_stop_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/stop — stop the current topic's session."""
        session = self._cmd.cmd_get_session(channel_id)
        if not session:
            await self._messenger.send_message(
                channel_id, "⚠️ No session linked to this topic."
            )
            return

        await self._messenger.send_message(
            channel_id, f"🔴 Session stopped: {session.name}"
        )
        await self._cmd.cmd_stop_session(channel_id)

    async def _handle_complete_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/complete — merge session branch into main and clean up."""
        session = self._cmd.cmd_get_session(channel_id)
        if not session:
            await self._messenger.send_message(
                channel_id, "⚠️ No session linked to this topic."
            )
            return

        await self._messenger.send_message(
            channel_id,
            f"⏳ Merging branch afk/{session.name} into main...",
            silent=True,
        )

        success, message = await self._cmd.cmd_complete_session(channel_id)

        if success:
            await self._messenger.send_message(
                "general", f"✅ {message}"
            )
        else:
            await self._messenger.send_message(
                channel_id, f"❌ {message}"
            )

    async def _handle_status_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/status — query current session status."""
        status = self._cmd.cmd_get_status(channel_id)
        if not status:
            await self._messenger.send_message(
                channel_id, "⚠️ No session linked to this topic."
            )
            return

        alive = "✅ Running" if status.agent_alive else "🔴 Stopped"
        tunnel_info = f"\nTunnel: {status.tunnel_url}" if status.tunnel_url else ""
        await self._messenger.send_message(
            channel_id,
            f"📊 Session: {status.name}\n"
            f"State: {status.state}\n"
            f"Process: {alive}\n"
            f"Project: {status.project_name} ({status.project_path})\n"
            f"Worktree: {status.worktree_path}{tunnel_info}",
        )

    async def _handle_tunnel_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/tunnel [stop] — start or stop a dev server tunnel."""
        # /tunnel stop
        if args and args[0].lower() == "stop":
            stopped = await self._cmd.cmd_stop_tunnel(channel_id)
            if stopped:
                await self._messenger.send_message(
                    channel_id, "🔴 Tunnel stopped."
                )
            else:
                await self._messenger.send_message(
                    channel_id, "No active tunnel to stop."
                )
            return

        # Already running — resend URL
        existing_url = self._cmd.cmd_get_tunnel_url(channel_id)
        if existing_url:
            await self._messenger.send_message(
                channel_id,
                f"Tunnel already running: {existing_url}",
                link_url=existing_url,
                link_label="Open tunnel",
            )
            return

        msg_id = await self._messenger.send_message(
            channel_id,
            "⏳ Starting dev server + cloudflared tunnel...",
            silent=True,
        )

        try:
            url = await self._cmd.cmd_start_tunnel(channel_id)
            await self._messenger.edit_message(
                channel_id, msg_id, "✅ Tunnel active",
            )
            await self._messenger.send_message(
                channel_id,
                url,
                link_url=url,
                link_label="Open in browser",
            )
        except RuntimeError as e:
            await self._messenger.edit_message(
                channel_id, msg_id, f"❌ Tunnel failed: {e}"
            )

    async def _handle_template_command(
        self, channel_id: str, args: list[str]
    ) -> None:
        """/template list — list available workspace templates."""
        if not args or args[0].lower() != "list":
            await self._messenger.send_message(
                channel_id, "Usage: /template list"
            )
            return

        templates = self._cmd.cmd_list_templates()
        if not templates:
            await self._messenger.send_message(
                channel_id, "No templates available."
            )
            return

        lines = []
        for t in templates:
            agent_info = f" (agent: {t['agent']})" if t.get("agent") else ""
            lines.append(f"📋 {t['name']}{agent_info} — {t['description']}")
        await self._messenger.send_message(channel_id, "\n".join(lines))

    async def _handle_unknown_command(
        self, channel_id: str, command_text: str
    ) -> None:
        """Show available commands when an unknown command is used."""
        await self._messenger.send_message(
            channel_id,
            f"❓ Unknown command: {command_text}\n\n"
            "Available commands:\n"
            "/project add|list|remove|init — manage projects\n"
            "/new <project> [-v] [--agent <name>] [--template <name>] — create session\n"
            "/sessions — list active sessions\n"
            "/stop — stop current session\n"
            "/complete — merge & cleanup session\n"
            "/status — check session state\n"
            "/tunnel — start dev server tunnel (stop: /tunnel stop)\n"
            "/template list — list workspace templates",
        )

    async def _handle_permission_response(
        self, channel_id: str, request_id: str, choice: str
    ) -> None:
        """Handle permission button response."""
        allowed = choice == "allow"
        await self._cmd.cmd_permission_response(channel_id, request_id, allowed)
