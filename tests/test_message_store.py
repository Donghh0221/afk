from __future__ import annotations

import time

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
