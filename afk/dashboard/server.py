from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from afk.core.session_manager import SessionManager
    from afk.dashboard.message_store import MessageStore

logger = logging.getLogger(__name__)

_HTML_PATH = Path(__file__).parent / "index.html"


def _build_app(
    session_manager: SessionManager,
    message_store: MessageStore,
    log_file: str,
) -> web.Application:
    app = web.Application()
    app["sm"] = session_manager
    app["ms"] = message_store
    app["log_file"] = log_file

    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/sessions", _handle_sessions)
    app.router.add_get("/api/sessions/{channel_id}/messages", _handle_messages)
    app.router.add_get("/api/logs", _handle_logs)
    return app


async def _handle_index(request: web.Request) -> web.Response:
    html = _HTML_PATH.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def _handle_sessions(request: web.Request) -> web.Response:
    sm: SessionManager = request.app["sm"]
    sessions = sm.list_sessions()
    data = []
    for s in sessions:
        data.append({
            "name": s.name,
            "channel_id": s.channel_id,
            "project_name": s.project_name,
            "state": s.state,
            "created_at": getattr(s, "created_at", None),
        })
    return web.json_response(data)


async def _handle_messages(request: web.Request) -> web.Response:
    ms: MessageStore = request.app["ms"]
    channel_id = request.match_info["channel_id"]
    try:
        after = float(request.query.get("after", "0"))
    except (ValueError, TypeError):
        after = 0
    try:
        limit = int(request.query.get("limit", "100"))
    except (ValueError, TypeError):
        limit = 100
    limit = min(limit, 500)
    messages = ms.get_messages(channel_id, after=after, limit=limit)
    return web.json_response(messages)


async def _handle_logs(request: web.Request) -> web.Response:
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


class DashboardServer:
    """aiohttp-based dashboard web server."""

    def __init__(
        self,
        session_manager: SessionManager,
        message_store: MessageStore,
        log_file: str,
        port: int = 7777,
    ) -> None:
        self._app = _build_app(session_manager, message_store, log_file)
        self._port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self._port)
        await site.start()
        logger.info("Dashboard running at http://localhost:%d", self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            logger.info("Dashboard stopped")
