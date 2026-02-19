"""Per-session logging: lifecycle logger + raw stdout file writer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import IO


class SessionLogger:
    """Manages per-session log files.

    Provides:
    - logger: Python Logger for lifecycle/state events (-> session.log)
    - write_raw(): writes raw agent stdout lines (-> agent.raw.log)
    - stderr_log_path: path for adapters to drain subprocess stderr
    """

    def __init__(self, log_dir: Path, session_name: str) -> None:
        self._log_dir = log_dir
        self._session_name = session_name
        self._raw_log_file: IO[str] | None = None
        self._handler: logging.FileHandler | None = None
        self._logger: logging.Logger | None = None

        self._log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    @property
    def stderr_log_path(self) -> Path:
        return self._log_dir / "agent.stderr.log"

    def start(self) -> None:
        """Open file handles and attach the per-session Python logger."""
        # Per-session logger -> session.log
        self._logger = logging.getLogger(f"afk.session.{self._session_name}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

        self._handler = logging.FileHandler(
            self._log_dir / "session.log", encoding="utf-8",
        )
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s"),
        )
        self._logger.addHandler(self._handler)

        # Raw stdout tee (append mode — survives recovery)
        self._raw_log_file = open(
            self._log_dir / "agent.raw.log", "a", encoding="utf-8",
        )

    @property
    def logger(self) -> logging.Logger:
        if self._logger is None:
            raise RuntimeError("SessionLogger not started")
        return self._logger

    def write_raw(self, line: str) -> None:
        """Append a raw agent stdout line to agent.raw.log."""
        if self._raw_log_file and not self._raw_log_file.closed:
            self._raw_log_file.write(line)
            self._raw_log_file.flush()

    def close(self) -> None:
        """Close all file handles. Safe to call multiple times."""
        if self._raw_log_file and not self._raw_log_file.closed:
            self._raw_log_file.close()
            self._raw_log_file = None

        if self._handler and self._logger:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None
