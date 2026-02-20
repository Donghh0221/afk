from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import pytest

from afk.core.events import EventBus


@dataclass
class FakeEvent:
    value: int


@dataclass
class OtherEvent:
    text: str


class TestEventBus:
    def test_subscribe_and_publish(self):
        bus = EventBus()
        queue = bus.subscribe(FakeEvent)
        bus.publish(FakeEvent(value=42))
        assert not queue.empty()
        assert queue.get_nowait().value == 42

    def test_multiple_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe(FakeEvent)
        q2 = bus.subscribe(FakeEvent)
        bus.publish(FakeEvent(value=1))
        assert q1.get_nowait().value == 1
        assert q2.get_nowait().value == 1

    def test_publish_no_subscribers(self):
        bus = EventBus()
        # Should not raise
        bus.publish(FakeEvent(value=99))

    def test_publish_different_types_isolated(self):
        bus = EventBus()
        q_fake = bus.subscribe(FakeEvent)
        q_other = bus.subscribe(OtherEvent)
        bus.publish(FakeEvent(value=1))
        assert not q_fake.empty()
        assert q_other.empty()

    def test_unsubscribe(self):
        bus = EventBus()
        queue = bus.subscribe(FakeEvent)
        bus.unsubscribe(FakeEvent, queue)
        bus.publish(FakeEvent(value=1))
        assert queue.empty()

    def test_unsubscribe_unknown_queue(self):
        bus = EventBus()
        other_queue: asyncio.Queue = asyncio.Queue()
        # Should not raise
        bus.unsubscribe(FakeEvent, other_queue)

    def test_queue_full_logs_warning(self, caplog):
        bus = EventBus()
        queue = bus.subscribe(FakeEvent)
        # Replace the queue with a size-1 queue
        bus._subscribers[FakeEvent] = [asyncio.Queue(maxsize=1)]
        small_queue = bus._subscribers[FakeEvent][0]

        small_queue.put_nowait(FakeEvent(value=0))  # Fill it
        with caplog.at_level(logging.WARNING):
            bus.publish(FakeEvent(value=1))  # Should warn, not raise
        assert "queue full" in caplog.text.lower()

    async def test_iter_events(self):
        bus = EventBus()
        received = []

        async def consumer():
            async for ev in bus.iter_events(FakeEvent):
                received.append(ev.value)
                if ev.value == 2:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        bus.publish(FakeEvent(value=1))
        bus.publish(FakeEvent(value=2))
        await task

        assert received == [1, 2]

    async def test_iter_events_cleanup_on_cancel(self):
        bus = EventBus()

        async def consumer():
            async for _ in bus.iter_events(FakeEvent):
                pass

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        # There should be 1 subscriber
        assert len(bus._subscribers.get(FakeEvent, [])) == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # After cancel, the subscription should be cleaned up
        assert len(bus._subscribers.get(FakeEvent, [])) == 0
