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


async def commit_worktree_changes(
    worktree_path: str,
    session_name: str,
) -> tuple[bool, str]:
    """Stage and commit all changes in a worktree.

    Returns (had_changes, message).
    If there are no changes, returns (False, ...).
    """
    # Stage all changes (new, modified, deleted)
    code, stdout, stderr = await _run_git(
        ["add", "-A"],
        cwd=worktree_path,
    )
    if code != 0:
        return False, f"git add failed: {stderr}"

    # Check if there's anything to commit
    code, stdout, stderr = await _run_git(
        ["diff", "--cached", "--quiet"],
        cwd=worktree_path,
    )
    if code == 0:
        # No staged changes
        return False, "No changes to commit."

    # Commit
    code, stdout, stderr = await _run_git(
        ["commit", "-m", f"Session {session_name} changes"],
        cwd=worktree_path,
    )
    if code != 0:
        return False, f"git commit failed: {stderr}"

    logger.info("Committed worktree changes for session %s", session_name)
    return True, stdout


async def merge_branch_to_main(
    project_path: str,
    branch_name: str,
    worktree_path: str,
) -> tuple[bool, str]:
    """Rebase session branch onto main, then fast-forward merge.

    The rebase runs inside the worktree (which has the branch checked out).
    After a successful rebase the worktree is removed so that the branch
    is no longer "in use", and then main is fast-forwarded.

    Returns (success, message). On conflict the rebase is aborted
    so both main and the session branch stay clean.
    """
    # Abort any in-progress rebase inside the worktree (defensive cleanup)
    await _run_git(["rebase", "--abort"], cwd=worktree_path)

    # Rebase onto main *inside* the worktree — avoids the
    # "branch is already used by worktree" error.
    code, stdout, stderr = await _run_git(
        ["rebase", "main"],
        cwd=worktree_path,
    )
    if code != 0:
        await _run_git(["rebase", "--abort"], cwd=worktree_path)
        return False, stderr or stdout

    # Remove the worktree so the branch is no longer locked
    await remove_worktree_after_merge(project_path, worktree_path, None)

    # Abort any in-progress merge on main (defensive cleanup)
    await _run_git(["merge", "--abort"], cwd=project_path)

    # Fast-forward main to the rebased branch
    code, stdout, stderr = await _run_git(
        ["merge", "--ff-only", branch_name],
        cwd=project_path,
    )
    if code != 0:
        return False, stderr or stdout
    return True, stdout


async def remove_worktree_after_merge(
    project_path: str,
    worktree_path: str,
    branch_name: str | None,
) -> None:
    """Remove worktree and optionally delete the branch.

    If *branch_name* is given, delete it with 'branch -d' (safe, since it
    should already be merged).  Pass ``None`` to skip branch deletion
    (e.g. when the worktree is removed before the merge).
    """
    code, _, stderr = await _run_git(
        ["worktree", "remove", "--force", worktree_path],
        cwd=project_path,
    )
    if code != 0:
        logger.warning(
            "git worktree remove failed for %s: %s", worktree_path, stderr
        )

    if branch_name is not None:
        code, _, stderr = await _run_git(
            ["branch", "-d", branch_name],
            cwd=project_path,
        )
        if code != 0:
            logger.warning(
                "git branch -d failed for %s: %s", branch_name, stderr
            )


async def delete_branch(project_path: str, branch_name: str) -> None:
    """Delete a merged branch. Best-effort: errors are logged."""
    code, _, stderr = await _run_git(
        ["branch", "-d", branch_name],
        cwd=project_path,
    )
    if code != 0:
        logger.warning("git branch -d failed for %s: %s", branch_name, stderr)


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
