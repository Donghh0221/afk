from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import AsyncIterator

from afk.ports.agent import AgentPort

logger = logging.getLogger(__name__)


class ClaudeCodeAgent(AgentPort):
    """AgentPort implementation wrapping the Claude Code CLI (stream-json protocol)."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None
        self._alive = False
        self._stderr_task: asyncio.Task | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def is_alive(self) -> bool:
        return self._alive and self._process is not None and self._process.returncode is None

    async def start(
        self,
        working_dir: str,
        session_id: str | None = None,
        stderr_log_path: Path | None = None,
    ) -> None:
        """Start Claude Code in headless mode."""
        claude_path = shutil.which("claude")
        if not claude_path:
            raise RuntimeError("claude CLI not found in PATH")

        cmd = [
            claude_path,
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]

        if session_id:
            cmd.extend(["--resume", "--session-id", session_id])

        logger.info("Starting Claude Code: %s (cwd=%s)", " ".join(cmd), working_dir)

        # Remove CLAUDECODE env var to bypass nested execution block
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env=env,
        )
        self._alive = True

        if stderr_log_path and self._process.stderr:
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(self._process.stderr, stderr_log_path)
            )

    async def _drain_stderr(
        self,
        stream: asyncio.StreamReader,
        log_path: Path,
    ) -> None:
        """Read stderr line-by-line and append to log file."""
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    f.write(line.decode("utf-8", errors="replace"))
                    f.flush()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("stderr drain ended", exc_info=True)

    async def send_message(self, text: str) -> None:
        """Send user message to stdin in stream-json format."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Claude process not started")

        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()
        logger.debug("Sent to Claude: %s", text[:100])

    async def send_permission_response(
        self, request_id: str, allowed: bool
    ) -> None:
        """Forward permission response to stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Claude process not started")

        msg = {
            "type": "permission_response",
            "id": request_id,
            "allowed": allowed,
        }
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()
        logger.debug("Sent permission response: %s -> %s", request_id, allowed)

    async def read_responses(self) -> AsyncIterator[dict]:
        """Read and parse stream-json responses line by line from stdout."""
        if not self._process or not self._process.stdout:
            return

        while self.is_alive:
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
                logger.warning("Non-JSON output from Claude: %s", line_str[:200])
                continue

            # Extract session_id from init message
            if data.get("type") == "system" and data.get("session_id"):
                self._session_id = data["session_id"]
                logger.info("Claude session ID: %s", self._session_id)

            yield data

        self._alive = False

    async def stop(self) -> None:
        """Stop the process."""
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

        if self._process:
            self._alive = False
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            self._process = None
            logger.info("Claude process stopped")
