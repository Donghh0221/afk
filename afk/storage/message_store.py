from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Message:
    timestamp: float
    role: str  # user | assistant | system | tool | result | file | permission
    text: str
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "timestamp": self.timestamp,
            "role": self.role,
            "text": self.text,
        }
        if self.meta:
            d["meta"] = self.meta
        return d


class MessageStore:
    """Per-channel message history with optional JSONL persistence.

    Each channel is stored as a separate ``.jsonl`` file under
    ``data_dir/messages/``.  Messages are append-only on disk and
    kept in memory with a bounded deque for fast reads.

    If *data_dir* is ``None`` the store operates in-memory only
    (useful for tests).
    """

    MAX_PER_SESSION = 500

    def __init__(self, data_dir: Path | None = None) -> None:
        self._store: dict[str, deque[Message]] = {}
        self._dir: Path | None = None
        if data_dir is not None:
            self._dir = data_dir / "messages"
            self._dir.mkdir(parents=True, exist_ok=True)
            self._load_all()

    # -- Public API (unchanged interface) -----------------------------------

    def append(
        self,
        channel_id: str,
        role: str,
        text: str,
        meta: dict | None = None,
    ) -> None:
        if channel_id not in self._store:
            self._store[channel_id] = deque(maxlen=self.MAX_PER_SESSION)
        msg = Message(
            timestamp=time.time(),
            role=role,
            text=text,
            meta=meta or {},
        )
        self._store[channel_id].append(msg)
        self._persist(channel_id, msg)

    def get_messages(
        self,
        channel_id: str,
        after: float = 0,
        limit: int = 100,
    ) -> list[dict]:
        msgs = self._store.get(channel_id, deque())
        result = []
        for m in msgs:
            if m.timestamp > after:
                result.append(m.to_dict())
                if len(result) >= limit:
                    break
        return result

    def channels(self) -> list[str]:
        return list(self._store.keys())

    # -- Persistence --------------------------------------------------------

    def _channel_path(self, channel_id: str) -> Path | None:
        if self._dir is None:
            return None
        # Sanitize channel_id for use as filename
        safe = channel_id.replace("/", "_").replace(":", "_")
        return self._dir / f"{safe}.jsonl"

    def _persist(self, channel_id: str, msg: Message) -> None:
        path = self._channel_path(channel_id)
        if path is None:
            return
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to persist message for %s", channel_id, exc_info=True)

    def _load_all(self) -> None:
        """Load all existing JSONL files into memory."""
        if self._dir is None:
            return
        for p in self._dir.glob("*.jsonl"):
            channel_id = p.stem.replace("_", ":", 1)  # restore first : from web:xxx
            try:
                dq: deque[Message] = deque(maxlen=self.MAX_PER_SESSION)
                with p.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        d = json.loads(line)
                        dq.append(Message(
                            timestamp=d["timestamp"],
                            role=d["role"],
                            text=d["text"],
                            meta=d.get("meta", {}),
                        ))
                self._store[channel_id] = dq
                logger.debug(
                    "Loaded %d messages for channel %s", len(dq), channel_id,
                )
            except Exception:
                logger.warning("Failed to load messages from %s", p, exc_info=True)
