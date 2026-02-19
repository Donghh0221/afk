from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Message:
    timestamp: float
    role: str  # user | assistant | system | tool | result
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
    """Per-session in-memory message history."""

    MAX_PER_SESSION = 500

    def __init__(self) -> None:
        self._store: dict[str, deque[Message]] = {}

    def append(
        self,
        channel_id: str,
        role: str,
        text: str,
        meta: dict | None = None,
    ) -> None:
        if channel_id not in self._store:
            self._store[channel_id] = deque(maxlen=self.MAX_PER_SESSION)
        self._store[channel_id].append(
            Message(
                timestamp=time.time(),
                role=role,
                text=text,
                meta=meta or {},
            )
        )

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
