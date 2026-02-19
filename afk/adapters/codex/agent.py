from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from typing import AsyncIterator

from afk.ports.agent import AgentPort

logger = logging.getLogger(__name__)


class CodexAgent(AgentPort):
    """AgentPort implementation wrapping the OpenAI Codex CLI (exec + NDJSON).

    Unlike Claude Code's persistent stdin/stdout stream, Codex uses a
    fire-and-complete model: each ``codex exec`` invocation runs a single
    task and exits. Follow-up messages use ``codex exec resume --last``.

    This adapter hides that difference behind the AgentPort interface by
    managing an internal asyncio.Queue that outlives individual Codex
    subprocesses.
    """

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None  # Codex thread_id
        self._started = False
        self._working_dir: str | None = None
        self._event_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._first_message = True

    # -- Properties ------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def is_alive(self) -> bool:
        # Logically alive between start() and stop(), regardless of subprocess
        return self._started

    # -- Lifecycle -------------------------------------------------------------

    async def start(
        self, working_dir: str, session_id: str | None = None
    ) -> None:
        """Validate codex binary and prepare for first message."""
        if not shutil.which("codex"):
            raise RuntimeError("codex CLI not found in PATH")

        self._working_dir = working_dir
        self._session_id = session_id
        self._started = True
        self._first_message = session_id is None

        # Synthetic system event so SessionManager publishes AgentSystemEvent
        self._event_queue.put_nowait({
            "type": "system",
            "session_id": session_id,
        })
        logger.info("Codex agent ready (cwd=%s)", working_dir)

    async def send_message(self, text: str) -> None:
        """Spawn a codex exec process for this message."""
        if not self._started:
            raise RuntimeError("Codex agent not started")

        await self._wait_for_process()

        codex_path = shutil.which("codex")
        if not codex_path:
            raise RuntimeError("codex CLI not found in PATH")

        cmd = [codex_path, "exec"]

        if self._first_message:
            cmd.extend([text, "--json", "--full-auto"])
            self._first_message = False
        else:
            cmd.extend(["resume", "--last", text, "--json", "--full-auto"])

        logger.info("Starting Codex: %s (cwd=%s)", " ".join(cmd), self._working_dir)

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._working_dir,
        )

        self._reader_task = asyncio.create_task(self._read_process_output())

    async def send_permission_response(
        self, request_id: str, allowed: bool
    ) -> None:
        # Codex --full-auto does not use permission requests
        pass

    async def read_responses(self) -> AsyncIterator[dict]:
        """Long-lived async iterator over the internal event queue."""
        while self._started:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            if event is None:
                break  # Shutdown sentinel
            yield event

    async def stop(self) -> None:
        """Stop the agent and any running Codex process."""
        self._started = False
        self._event_queue.put_nowait(None)  # Unblock read_responses()

        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        self._process = None
        self._reader_task = None
        logger.info("Codex agent stopped")

    # -- Internal helpers ------------------------------------------------------

    async def _wait_for_process(self) -> None:
        """Wait for the current Codex process to finish, if any."""
        if self._process is not None and self._process.returncode is None:
            await self._process.wait()
        if self._reader_task is not None:
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        self._process = None

    async def _read_process_output(self) -> None:
        """Read NDJSON from the current Codex process and push mapped events."""
        if not self._process or not self._process.stdout:
            return

        start_time = time.monotonic()

        while True:
            try:
                line = await self._process.stdout.readline()
            except Exception:
                break

            if not line:
                break

            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
            except json.JSONDecodeError:
                logger.warning("Non-JSON output from Codex: %s", line_str[:200])
                continue

            event_type = data.get("type")

            if event_type == "thread.started":
                thread_id = data.get("thread_id")
                if thread_id:
                    self._session_id = thread_id
                    logger.info("Codex thread ID: %s", thread_id)

            elif event_type == "item.completed":
                item = data.get("item", {})
                blocks = _map_item_to_content_blocks(item)
                if blocks:
                    self._event_queue.put_nowait({
                        "type": "assistant",
                        "content": blocks,
                    })

            elif event_type == "turn.completed":
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                self._event_queue.put_nowait({
                    "type": "result",
                    "total_cost_usd": 0,
                    "duration_ms": elapsed_ms,
                })

            elif event_type == "turn.failed":
                error_msg = data.get("error", "Unknown error")
                self._event_queue.put_nowait({
                    "type": "assistant",
                    "content": [{"type": "text", "text": f"Error: {error_msg}"}],
                })

            elif event_type == "error":
                error_msg = data.get("message", "Unknown error")
                self._event_queue.put_nowait({
                    "type": "assistant",
                    "content": [{"type": "text", "text": f"Error: {error_msg}"}],
                })


# -- Event mapping (Codex items → content_blocks) -----------------------------


def _map_item_to_content_blocks(item: dict) -> list[dict]:
    """Map a Codex item to the content_blocks format expected by EventRenderer.

    EventRenderer expects blocks with type: text | tool_use | tool_result.
    """
    item_type = item.get("type")
    blocks: list[dict] = []

    if item_type == "agent_message":
        text = item.get("text", "")
        if text:
            blocks.append({"type": "text", "text": text})

    elif item_type == "reasoning":
        text = item.get("text", "")
        if text:
            blocks.append({"type": "text", "text": f"[reasoning] {text}"})

    elif item_type == "command_execution":
        command = item.get("command", "")
        output = item.get("aggregated_output", "")
        exit_code = item.get("exit_code", 0)
        blocks.append({
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": command},
        })
        blocks.append({
            "type": "tool_result",
            "content": output,
            "is_error": exit_code != 0,
        })

    elif item_type == "file_change":
        changes = item.get("changes", [])
        summary_parts = []
        for change in changes:
            path = change.get("path", "unknown")
            kind = change.get("change_kind", "modify")
            summary_parts.append(f"{kind}: {path}")
        if summary_parts:
            blocks.append({
                "type": "tool_use",
                "name": "FileChange",
                "input": {"changes": "\n".join(summary_parts)},
            })

    elif item_type == "mcp_tool_call":
        tool_name = item.get("tool_name", "mcp_tool")
        blocks.append({
            "type": "tool_use",
            "name": f"MCP:{tool_name}",
            "input": item.get("arguments", {}),
        })
        content = item.get("content")
        if content:
            blocks.append({
                "type": "tool_result",
                "content": str(content),
                "is_error": False,
            })

    elif item_type == "web_search":
        query = item.get("query", "")
        blocks.append({
            "type": "tool_use",
            "name": "WebSearch",
            "input": {"query": query},
        })

    elif item_type == "error":
        text = item.get("text", item.get("message", "Unknown error"))
        blocks.append({"type": "text", "text": f"Error: {text}"})

    return blocks
