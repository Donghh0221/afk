"""Generate commit messages using the Claude Code CLI."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

logger = logging.getLogger(__name__)


async def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


async def generate_commit_message(worktree_path: str) -> str:
    """Use Claude Code CLI to generate a commit message from staged diff.

    Falls back to a generic message on any failure.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        return "Update files"

    # Get the staged diff (truncate to avoid overwhelming the prompt)
    code, diff_output, _ = await _run_git(
        ["diff", "--cached", "--stat"],
        cwd=worktree_path,
    )
    if code != 0 or not diff_output:
        return "Update files"

    prompt = (
        "Based on the following git diff stat, write a concise commit message "
        "(single line, max 72 chars, imperative mood, no quotes). "
        "Just output the message, nothing else.\n\n"
        f"{diff_output}"
    )

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = await asyncio.create_subprocess_exec(
            claude_path, "-p", "--no-session-persistence", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=worktree_path,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        msg = stdout.decode().strip()
        if msg and proc.returncode == 0:
            # Take first line only, enforce max length
            first_line = msg.splitlines()[0].strip().strip('"\'')
            return first_line[:72] if first_line else "Update files"
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("Claude commit message generation failed: %s", e)

    return "Update files"
