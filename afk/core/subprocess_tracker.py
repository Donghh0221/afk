"""Global subprocess tracker — ensures child processes are terminated on exit.

Long-running subprocesses (dev servers, tunnels, agents) are tracked here.
An ``atexit`` handler sends SIGTERM to all tracked PIDs on daemon exit,
including abnormal exits (unhandled exceptions).  PIDs are also persisted
to disk so that orphans from a crashed daemon can be cleaned up on restart.
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

_tracked_pids: set[int] = set()
_pid_file: Path | None = None


def set_pid_file(path: str | Path) -> None:
    """Set the path for persisting tracked PIDs (call once at startup)."""
    global _pid_file
    _pid_file = Path(path)


def track(pid: int) -> None:
    """Register a long-running subprocess PID."""
    _tracked_pids.add(pid)
    _save()


def untrack(pid: int) -> None:
    """Unregister a subprocess PID (stopped normally)."""
    _tracked_pids.discard(pid)
    _save()


def kill_all() -> None:
    """Send SIGTERM to all tracked PIDs (called by atexit)."""
    for pid in list(_tracked_pids):
        try:
            os.kill(pid, signal.SIGTERM)
            logger.debug("Sent SIGTERM to tracked PID %d", pid)
        except ProcessLookupError:
            pass
        except OSError as e:
            logger.debug("Failed to signal PID %d: %s", pid, e)
    _tracked_pids.clear()
    _save()


def cleanup_stale_pids() -> None:
    """Kill orphan processes from a previous crashed daemon (read PID file)."""
    if not _pid_file or not _pid_file.exists():
        return
    killed = 0
    try:
        for line in _pid_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pid = int(line)
                os.kill(pid, signal.SIGTERM)
                killed += 1
                logger.info("Killed stale subprocess PID %d", pid)
            except ValueError:
                pass
            except ProcessLookupError:
                pass  # Already dead
            except OSError as e:
                logger.debug("Could not kill stale PID %s: %s", line, e)
    except OSError:
        pass
    if killed:
        logger.info("Cleaned up %d stale subprocess(es)", killed)
    # Remove the stale PID file
    try:
        _pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def _save() -> None:
    """Persist current tracked PIDs to disk."""
    if not _pid_file:
        return
    try:
        _pid_file.parent.mkdir(parents=True, exist_ok=True)
        _pid_file.write_text(
            "\n".join(str(pid) for pid in _tracked_pids) + "\n"
            if _tracked_pids else ""
        )
    except OSError:
        pass


# Register the atexit handler at import time — covers unhandled exceptions
# and normal exits.  SIGKILL cannot be caught; cleanup_stale_pids() on the
# next startup handles that case.
atexit.register(kill_all)
