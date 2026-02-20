from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator

from afk.ports.agent import AgentPort

logger = logging.getLogger(__name__)

# Polling interval for checking research status (seconds)
_POLL_INTERVAL = 10.0


class DeepResearchAgent(AgentPort):
    """AgentPort implementation wrapping the OpenAI Deep Research API.

    Unlike subprocess-based agents, this adapter communicates via HTTP:
    - ``send_message()`` submits a background research request and starts polling
    - ``read_responses()`` yields events from an internal asyncio.Queue
    - ``stop()`` cancels any in-progress polling

    Events emitted follow the same format as other agents (assistant/result),
    so SessionManager._publish_agent_event handles them without changes.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "o4-mini-deep-research",
        max_tool_calls: int | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tool_calls = max_tool_calls

        self._client = None  # AsyncOpenAI, created in start()
        self._started = False
        self._working_dir: str | None = None
        self._response_id: str | None = None
        self._event_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._poll_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    # -- Properties ------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._response_id

    @property
    def is_alive(self) -> bool:
        return self._started

    # -- Lifecycle -------------------------------------------------------------

    async def start(
        self,
        working_dir: str,
        session_id: str | None = None,
        stderr_log_path: Path | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=self._api_key)
        self._working_dir = working_dir
        self._response_id = session_id
        self._started = True

        # Synthetic system event so SessionManager publishes AgentSystemEvent
        self._event_queue.put_nowait({
            "type": "system",
            "session_id": session_id,
        })
        logger.info(
            "Deep Research agent ready (model=%s, cwd=%s)",
            self._model, working_dir,
        )

    async def send_message(self, text: str) -> None:
        if not self._started or self._client is None:
            raise RuntimeError("Deep Research agent not started")

        # Cancel any previous polling task
        if self._poll_task and not self._poll_task.done():
            self._stop_event.set()
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._stop_event.clear()

        self._poll_task = asyncio.create_task(self._run_research(text))

    async def send_permission_response(
        self, request_id: str, allowed: bool
    ) -> None:
        # Deep Research runs autonomously — no permission prompts
        pass

    async def read_responses(self) -> AsyncIterator[dict]:
        while self._started:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            if event is None:
                break  # Shutdown sentinel
            yield event

    async def stop(self) -> None:
        self._started = False
        self._stop_event.set()
        self._event_queue.put_nowait(None)  # Unblock read_responses()

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        if self._client:
            await self._client.close()
            self._client = None

        self._poll_task = None
        logger.info("Deep Research agent stopped")

    # -- Internal: research execution -----------------------------------------

    async def _run_research(self, prompt: str) -> None:
        """Submit deep research request and poll until completion."""
        assert self._client is not None
        start_time = time.monotonic()

        try:
            # 1. Notify: starting
            self._event_queue.put_nowait({
                "type": "assistant",
                "content": [{"type": "text", "text": (
                    f"Starting deep research (model: {self._model})...\n"
                    f"Query: {prompt[:200]}"
                )}],
            })

            # 2. Submit background request
            tools: list[dict] = [{"type": "web_search_preview"}]
            create_kwargs: dict = {
                "model": self._model,
                "input": prompt,
                "tools": tools,
                "background": True,
            }
            if self._max_tool_calls is not None:
                create_kwargs["max_tool_calls"] = self._max_tool_calls

            response = await self._client.responses.create(**create_kwargs)
            self._response_id = response.id
            logger.info(
                "Deep Research submitted: id=%s status=%s",
                response.id, response.status,
            )

            # 3. Poll until terminal state
            poll_count = 0
            while response.status in ("queued", "in_progress"):
                if self._stop_event.is_set():
                    logger.info("Deep Research polling cancelled")
                    return

                await asyncio.sleep(_POLL_INTERVAL)
                poll_count += 1
                response = await self._client.responses.retrieve(response.id)

                # Periodic status update (every 6 polls = ~60s)
                if poll_count % 6 == 0:
                    elapsed = int(time.monotonic() - start_time)
                    self._event_queue.put_nowait({
                        "type": "assistant",
                        "content": [{"type": "text", "text": (
                            f"Researching... ({elapsed}s elapsed, "
                            f"status: {response.status})"
                        )}],
                    })

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            # 4. Handle terminal states
            if response.status != "completed":
                error_detail = ""
                if response.error:
                    error_detail = f": {response.error}"
                self._event_queue.put_nowait({
                    "type": "assistant",
                    "content": [{"type": "text", "text": (
                        f"Deep research {response.status}{error_detail}"
                    )}],
                })
                self._event_queue.put_nowait({
                    "type": "result",
                    "total_cost_usd": 0,
                    "duration_ms": elapsed_ms,
                })
                return

            # 5. Extract report text and citations
            report_text = response.output_text or ""
            citations = self._extract_citations(response)

            if citations:
                sources_section = "\n\n---\n\n## Sources\n\n"
                seen_urls: set[str] = set()
                for c in citations:
                    if c["url"] not in seen_urls:
                        seen_urls.add(c["url"])
                        sources_section += f"- [{c['title']}]({c['url']})\n"
                report_text += sources_section

            # 6. Save report to worktree
            report_path = self._save_report(report_text)

            # 6a. Emit file output event for auto-send
            self._event_queue.put_nowait({
                "type": "file_output",
                "file_path": str(report_path),
                "file_name": report_path.name,
            })

            # 7. Git commit
            commit_msg = f"Add deep research report: {prompt[:60]}"
            await self._git_commit(commit_msg)

            # 8. Emit report preview + result
            preview = report_text[:1500]
            if len(report_text) > 1500:
                preview += f"\n\n... ({len(report_text)} chars total)"
            preview += f"\n\nReport saved: {report_path.name}"

            self._event_queue.put_nowait({
                "type": "assistant",
                "content": [{"type": "text", "text": preview}],
            })

            cost_usd = 0.0
            if hasattr(response, "usage") and response.usage:
                input_tokens = getattr(response.usage, "input_tokens", 0) or 0
                output_tokens = getattr(response.usage, "output_tokens", 0) or 0
                # o4-mini pricing: $1.10/M input, $4.40/M output
                cost_usd = (input_tokens * 1.10 + output_tokens * 4.40) / 1_000_000

            self._event_queue.put_nowait({
                "type": "result",
                "total_cost_usd": cost_usd,
                "duration_ms": elapsed_ms,
            })

            logger.info(
                "Deep Research completed: %d chars, %d citations, %.1fs",
                len(report_text), len(citations),
                elapsed_ms / 1000,
            )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Deep Research failed")
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            self._event_queue.put_nowait({
                "type": "assistant",
                "content": [{"type": "text", "text": (
                    f"Deep research failed. Check logs for details."
                )}],
            })
            self._event_queue.put_nowait({
                "type": "result",
                "total_cost_usd": 0,
                "duration_ms": elapsed_ms,
            })

    @staticmethod
    def _extract_citations(response) -> list[dict]:
        """Extract url_citation annotations from the response output."""
        citations: list[dict] = []
        for item in response.output:
            if getattr(item, "type", None) != "message":
                continue
            for block in getattr(item, "content", []):
                for ann in getattr(block, "annotations", []):
                    if getattr(ann, "type", None) == "url_citation":
                        citations.append({
                            "title": getattr(ann, "title", ""),
                            "url": getattr(ann, "url", ""),
                        })
        return citations

    def _save_report(self, text: str) -> Path:
        """Save report markdown to the worktree."""
        assert self._working_dir is not None
        worktree = Path(self._working_dir)

        # Use output/ if it exists (compatible with research template)
        output_dir = worktree / "output"
        if output_dir.is_dir():
            report_path = output_dir / "report.md"
        else:
            report_path = worktree / "report.md"

        report_path.write_text(text, encoding="utf-8")
        logger.info("Report saved: %s", report_path)
        return report_path

    async def _git_commit(self, message: str) -> None:
        """Stage all changes and commit in the worktree."""
        assert self._working_dir is not None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "add", "-A",
                cwd=self._working_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()

            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", message,
                cwd=self._working_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()
            logger.info("Report committed: %s", message)
        except Exception:
            logger.warning("Git commit failed", exc_info=True)
