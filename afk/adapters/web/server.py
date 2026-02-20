"""Web Control Plane — REST API + SSE for local browser-based session control.

Provides the same command capabilities as the Telegram control plane
but via HTTP endpoints and Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from afk.core.events import (
    AgentAssistantEvent,
    AgentInputRequestEvent,
    AgentPermissionRequestEvent,
    AgentResultEvent,
    AgentStoppedEvent,
    AgentSystemEvent,
    EventBus,
)

if TYPE_CHECKING:
    from afk.core.commands import Commands
    from afk.storage.message_store import MessageStore

logger = logging.getLogger(__name__)

_HTML_PATH = Path(__file__).parent / "index.html"

# All event types the SSE stream subscribes to.
_SSE_EVENT_TYPES: list[type] = [
    AgentSystemEvent,
    AgentAssistantEvent,
    AgentResultEvent,
    AgentStoppedEvent,
    AgentPermissionRequestEvent,
    AgentInputRequestEvent,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel_id() -> str:
    """Generate a web-scoped channel ID."""
    return f"web:{uuid.uuid4().hex[:12]}"


def _serialize_event(ev: object) -> dict:
    """Convert a typed event to a JSON-serializable dict for SSE."""
    name = type(ev).__name__
    data: dict = {"type": name, "channel_id": getattr(ev, "channel_id", "")}

    if isinstance(ev, AgentSystemEvent):
        data["agent_session_id"] = ev.agent_session_id

    elif isinstance(ev, AgentAssistantEvent):
        data["content_blocks"] = ev.content_blocks
        data["session_name"] = ev.session_name
        data["level"] = ev.level.value

    elif isinstance(ev, AgentResultEvent):
        data["cost_usd"] = ev.cost_usd
        data["duration_ms"] = ev.duration_ms

    elif isinstance(ev, AgentStoppedEvent):
        data["session_name"] = ev.session_name

    elif isinstance(ev, AgentPermissionRequestEvent):
        data["request_id"] = ev.request_id
        data["tool_name"] = ev.tool_name
        data["tool_input"] = ev.tool_input

    elif isinstance(ev, AgentInputRequestEvent):
        data["session_name"] = ev.session_name

    return data


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _handle_index(request: web.Request) -> web.Response:
    html = _HTML_PATH.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


# -- Sessions ---------------------------------------------------------------

async def _handle_sessions(request: web.Request) -> web.Response:
    """GET /api/sessions — list all active sessions."""
    cmd: Commands = request.app["cmd"]
    sessions = cmd.cmd_list_sessions()
    data = [
        {
            "name": s.name,
            "channel_id": s.channel_id,
            "project_name": s.project_name,
            "state": s.state,
        }
        for s in sessions
    ]
    return web.json_response(data)


async def _handle_new_session(request: web.Request) -> web.Response:
    """POST /api/sessions — create a new session."""
    cmd: Commands = request.app["cmd"]
    body = await request.json()
    project = body.get("project", "").strip()
    verbose = body.get("verbose", False)
    agent = body.get("agent") or None

    if not project:
        return web.json_response({"error": "project is required"}, status=400)

    channel_id = _make_channel_id()
    try:
        session = await cmd.cmd_new_session(
            project, verbose=verbose, channel_id=channel_id, agent=agent,
        )
        return web.json_response({
            "channel_id": session.channel_id,
            "name": session.name,
            "project_name": session.project_name,
            "worktree_path": session.worktree_path,
        })
    except (ValueError, RuntimeError) as e:
        return web.json_response({"error": str(e)}, status=400)


async def _handle_status(request: web.Request) -> web.Response:
    """GET /api/sessions/{channel_id}/status"""
    cmd: Commands = request.app["cmd"]
    channel_id = request.match_info["channel_id"]
    status = cmd.cmd_get_status(channel_id)
    if not status:
        return web.json_response({"error": "session not found"}, status=404)
    return web.json_response({
        "name": status.name,
        "state": status.state,
        "agent_alive": status.agent_alive,
        "project_name": status.project_name,
        "project_path": status.project_path,
        "worktree_path": status.worktree_path,
        "tunnel_url": status.tunnel_url,
    })


async def _handle_messages(request: web.Request) -> web.Response:
    """GET /api/sessions/{channel_id}/messages — message history."""
    ms: MessageStore = request.app["ms"]
    channel_id = request.match_info["channel_id"]
    try:
        after = float(request.query.get("after", "0"))
    except (ValueError, TypeError):
        after = 0
    try:
        limit = int(request.query.get("limit", "200"))
    except (ValueError, TypeError):
        limit = 200
    limit = min(limit, 500)
    messages = ms.get_messages(channel_id, after=after, limit=limit)
    return web.json_response(messages)


async def _handle_send_message(request: web.Request) -> web.Response:
    """POST /api/sessions/{channel_id}/message — send text to session."""
    cmd: Commands = request.app["cmd"]
    channel_id = request.match_info["channel_id"]
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text is required"}, status=400)

    session = cmd.cmd_get_session(channel_id)
    if not session:
        return web.json_response({"error": "session not found"}, status=404)
    if not session.agent.is_alive:
        return web.json_response({"error": "session has ended"}, status=409)
    if session.state == "waiting_permission":
        return web.json_response(
            {"error": "waiting for permission response"}, status=409,
        )

    ok = await cmd.cmd_send_message(channel_id, text)
    if not ok:
        return web.json_response({"error": "failed to send"}, status=500)
    return web.json_response({"ok": True})


async def _handle_stop(request: web.Request) -> web.Response:
    """POST /api/sessions/{channel_id}/stop"""
    cmd: Commands = request.app["cmd"]
    channel_id = request.match_info["channel_id"]
    ok = await cmd.cmd_stop_session(channel_id)
    if not ok:
        return web.json_response({"error": "session not found"}, status=404)
    return web.json_response({"ok": True})


async def _handle_complete(request: web.Request) -> web.Response:
    """POST /api/sessions/{channel_id}/complete"""
    cmd: Commands = request.app["cmd"]
    channel_id = request.match_info["channel_id"]
    success, message = await cmd.cmd_complete_session(channel_id)
    return web.json_response({"ok": success, "message": message})


async def _handle_permission(request: web.Request) -> web.Response:
    """POST /api/sessions/{channel_id}/permission"""
    cmd: Commands = request.app["cmd"]
    channel_id = request.match_info["channel_id"]
    body = await request.json()
    request_id = body.get("request_id", "")
    allowed = body.get("allowed", False)
    if not request_id:
        return web.json_response({"error": "request_id required"}, status=400)
    ok = await cmd.cmd_permission_response(channel_id, request_id, allowed)
    if not ok:
        return web.json_response({"error": "failed"}, status=500)
    return web.json_response({"ok": True})


# -- Projects ---------------------------------------------------------------

async def _handle_list_projects(request: web.Request) -> web.Response:
    """GET /api/projects"""
    cmd: Commands = request.app["cmd"]
    projects = cmd.cmd_list_projects()
    return web.json_response(projects)


async def _handle_add_project(request: web.Request) -> web.Response:
    """POST /api/projects"""
    cmd: Commands = request.app["cmd"]
    body = await request.json()
    name = body.get("name", "").strip()
    path = body.get("path", "").strip()
    if not name or not path:
        return web.json_response(
            {"error": "name and path are required"}, status=400,
        )
    ok, msg = cmd.cmd_add_project(name, path)
    return web.json_response({"ok": ok, "message": msg})


async def _handle_remove_project(request: web.Request) -> web.Response:
    """DELETE /api/projects/{name}"""
    cmd: Commands = request.app["cmd"]
    name = request.match_info["name"]
    ok, msg = cmd.cmd_remove_project(name)
    if not ok:
        return web.json_response({"error": msg}, status=404)
    return web.json_response({"ok": True, "message": msg})


# -- SSE --------------------------------------------------------------------

async def _handle_sse(request: web.Request) -> web.StreamResponse:
    """GET /api/events — Server-Sent Events stream for all agent events."""
    event_bus: EventBus = request.app["event_bus"]

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    # Subscribe to all event types — merge into a single queue.
    merged: asyncio.Queue = asyncio.Queue()
    subscriptions: list[tuple[type, asyncio.Queue]] = []

    async def _forward(event_type: type) -> None:
        q = event_bus.subscribe(event_type)
        subscriptions.append((event_type, q))
        try:
            while True:
                ev = await q.get()
                await merged.put(ev)
        except asyncio.CancelledError:
            pass

    tasks = [asyncio.create_task(_forward(et)) for et in _SSE_EVENT_TYPES]

    try:
        while True:
            ev = await merged.get()
            data = _serialize_event(ev)
            payload = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            await response.write(payload.encode("utf-8"))
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        for event_type, q in subscriptions:
            event_bus.unsubscribe(event_type, q)

    return response


# -- Logs -------------------------------------------------------------------

async def _handle_logs(request: web.Request) -> web.Response:
    """GET /api/logs — tail of the daemon log file."""
    try:
        lines_count = int(request.query.get("lines", "200"))
    except (ValueError, TypeError):
        lines_count = 200
    lines_count = min(lines_count, 1000)
    log_file = request.app["log_file"]
    try:
        path = Path(log_file)
        if not path.exists():
            return web.json_response({"lines": []})
        text = path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        tail = all_lines[-lines_count:]
        return web.json_response({"lines": tail})
    except Exception as e:
        logger.warning("Failed to read log file: %s", e)
        return web.json_response({"lines": ["Error reading log file"]})


# ---------------------------------------------------------------------------
# App factory & server class
# ---------------------------------------------------------------------------

def _build_app(
    commands: Commands,
    event_bus: EventBus,
    message_store: MessageStore,
    log_file: str,
) -> web.Application:
    app = web.Application()
    app["cmd"] = commands
    app["event_bus"] = event_bus
    app["ms"] = message_store
    app["log_file"] = log_file

    app.router.add_get("/", _handle_index)

    # Sessions
    app.router.add_get("/api/sessions", _handle_sessions)
    app.router.add_post("/api/sessions", _handle_new_session)
    app.router.add_get("/api/sessions/{channel_id}/status", _handle_status)
    app.router.add_get("/api/sessions/{channel_id}/messages", _handle_messages)
    app.router.add_post("/api/sessions/{channel_id}/message", _handle_send_message)
    app.router.add_post("/api/sessions/{channel_id}/stop", _handle_stop)
    app.router.add_post("/api/sessions/{channel_id}/complete", _handle_complete)
    app.router.add_post("/api/sessions/{channel_id}/permission", _handle_permission)

    # Projects
    app.router.add_get("/api/projects", _handle_list_projects)
    app.router.add_post("/api/projects", _handle_add_project)
    app.router.add_delete("/api/projects/{name}", _handle_remove_project)

    # SSE + logs
    app.router.add_get("/api/events", _handle_sse)
    app.router.add_get("/api/logs", _handle_logs)

    return app


class WebControlPlane:
    """aiohttp-based web control plane server."""

    def __init__(
        self,
        commands: Commands,
        event_bus: EventBus,
        message_store: MessageStore,
        log_file: str,
        port: int = 7777,
    ) -> None:
        self._app = _build_app(commands, event_bus, message_store, log_file)
        self._port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self._port)
        await site.start()
        logger.info("Web control plane running at http://localhost:%d", self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            logger.info("Web control plane stopped")
