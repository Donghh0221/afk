from __future__ import annotations

import asyncio
import logging
from pathlib import Path

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


async def is_git_repo(project_path: str) -> bool:
    """Return True if project_path is inside a git repository."""
    code, _, _ = await _run_git(["rev-parse", "--git-dir"], cwd=project_path)
    return code == 0


async def create_worktree(
    project_path: str,
    worktree_path: str,
    branch_name: str,
) -> None:
    """Create a new worktree at worktree_path on a new branch.

    Raises RuntimeError on failure.
    """
    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)

    code, stdout, stderr = await _run_git(
        ["worktree", "add", "-b", branch_name, worktree_path],
        cwd=project_path,
    )
    if code != 0:
        raise RuntimeError(f"git worktree add failed (exit {code}): {stderr}")
    logger.info("Created worktree: %s (branch=%s)", worktree_path, branch_name)


async def remove_worktree(
    project_path: str,
    worktree_path: str,
    branch_name: str,
) -> None:
    """Remove worktree and delete the associated branch.

    Best-effort: errors are logged but not raised.
    """
    code, _, stderr = await _run_git(
        ["worktree", "remove", "--force", worktree_path],
        cwd=project_path,
    )
    if code != 0:
        logger.warning(
            "git worktree remove failed for %s: %s", worktree_path, stderr
        )

    code, _, stderr = await _run_git(
        ["branch", "-D", branch_name],
        cwd=project_path,
    )
    if code != 0:
        logger.warning(
            "git branch -D failed for %s: %s", branch_name, stderr
        )


async def list_afk_worktrees(project_path: str) -> list[dict]:
    """List all worktrees whose branch starts with 'afk/'.

    Returns list of dicts with keys: path, branch.
    """
    code, stdout, _ = await _run_git(
        ["worktree", "list", "--porcelain"],
        cwd=project_path,
    )
    if code != 0:
        return []

    worktrees: list[dict] = []
    current: dict = {}
    for line in stdout.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):]}
        elif line.startswith("branch "):
            branch_ref = line[len("branch "):]
            branch = branch_ref.removeprefix("refs/heads/")
            current["branch"] = branch
            if branch.startswith("afk/"):
                worktrees.append(dict(current))
        elif line == "" and current:
            current = {}
    return worktrees
