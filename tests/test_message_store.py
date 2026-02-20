from __future__ import annotations

import time
from pathlib import Path

import pytest

from afk.storage.message_store import Message, MessageStore


class TestMessageToDict:
    def test_without_meta(self):
        m = Message(timestamp=1.0, role="user", text="hello")
        d = m.to_dict()
        assert d == {"timestamp": 1.0, "role": "user", "text": "hello"}
        assert "meta" not in d

    def test_with_meta(self):
        m = Message(timestamp=2.0, role="assistant", text="hi", meta={"k": "v"})
        d = m.to_dict()
        assert d["meta"] == {"k": "v"}


class TestMessageStore:
    """In-memory mode (no data_dir)."""

    def test_append_creates_channel(self):
        store = MessageStore()
        store.append("ch1", "user", "hello")
        assert "ch1" in store.channels()

    def test_append_to_existing_channel(self):
        store = MessageStore()
        store.append("ch1", "user", "a")
        store.append("ch1", "user", "b")
        msgs = store.get_messages("ch1")
        assert len(msgs) == 2

    def test_maxlen_eviction(self):
        store = MessageStore()
        for i in range(store.MAX_PER_SESSION + 50):
            store.append("ch1", "user", f"msg-{i}")
        msgs = store.get_messages("ch1", limit=1000)
        assert len(msgs) == store.MAX_PER_SESSION

    def test_get_messages_after_filter(self):
        store = MessageStore()
        store.append("ch1", "user", "old")
        # Manually set timestamp on first message to be old
        store._store["ch1"][0].timestamp = 100.0
        store.append("ch1", "user", "new")
        msgs = store.get_messages("ch1", after=100.0)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "new"

    def test_get_messages_limit(self):
        store = MessageStore()
        for i in range(10):
            store.append("ch1", "user", f"msg-{i}")
        msgs = store.get_messages("ch1", limit=3)
        assert len(msgs) == 3

    def test_get_messages_nonexistent_channel(self):
        store = MessageStore()
        msgs = store.get_messages("no-such-channel")
        assert msgs == []

    def test_channels(self):
        store = MessageStore()
        store.append("a", "user", "x")
        store.append("b", "user", "y")
        assert sorted(store.channels()) == ["a", "b"]


class TestMessageStorePersistence:
    """Persistent mode (with data_dir)."""

    def test_persist_and_reload(self, tmp_path: Path):
        store = MessageStore(tmp_path)
        store.append("ch1", "user", "hello")
        store.append("ch1", "assistant", "hi there")
        store.append("ch2", "user", "other channel")

        # Reload from disk
        store2 = MessageStore(tmp_path)
        msgs = store2.get_messages("ch1")
        assert len(msgs) == 2
        assert msgs[0]["text"] == "hello"
        assert msgs[1]["text"] == "hi there"
        assert len(store2.get_messages("ch2")) == 1

    def test_persist_with_meta(self, tmp_path: Path):
        store = MessageStore(tmp_path)
        store.append("ch1", "file", "report.md", meta={"file_path": "/tmp/report.md"})

        store2 = MessageStore(tmp_path)
        msgs = store2.get_messages("ch1")
        assert len(msgs) == 1
        assert msgs[0]["meta"]["file_path"] == "/tmp/report.md"

    def test_append_is_incremental(self, tmp_path: Path):
        store = MessageStore(tmp_path)
        store.append("ch1", "user", "first")

        # Append more to the same store
        store.append("ch1", "user", "second")

        # Reload — should have both
        store2 = MessageStore(tmp_path)
        msgs = store2.get_messages("ch1")
        assert len(msgs) == 2

    def test_web_channel_id_roundtrip(self, tmp_path: Path):
        store = MessageStore(tmp_path)
        store.append("web:abc123", "user", "from web")

        store2 = MessageStore(tmp_path)
        msgs = store2.get_messages("web:abc123")
        assert len(msgs) == 1
        assert msgs[0]["text"] == "from web"

    def test_maxlen_on_reload(self, tmp_path: Path):
        store = MessageStore(tmp_path)
        for i in range(store.MAX_PER_SESSION + 50):
            store.append("ch1", "user", f"msg-{i}")

        store2 = MessageStore(tmp_path)
        msgs = store2.get_messages("ch1", limit=1000)
        assert len(msgs) == store.MAX_PER_SESSION

    def test_channels_after_reload(self, tmp_path: Path):
        store = MessageStore(tmp_path)
        store.append("a", "user", "x")
        store.append("b", "user", "y")

        store2 = MessageStore(tmp_path)
        assert sorted(store2.channels()) == ["a", "b"]

    def test_creates_messages_dir(self, tmp_path: Path):
        data_dir = tmp_path / "nested" / "data"
        store = MessageStore(data_dir)
        store.append("ch1", "user", "hello")
        assert (data_dir / "messages").is_dir()
