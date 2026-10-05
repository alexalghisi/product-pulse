from __future__ import annotations

import time

import pytest
import redis

from product_pulse.ingestion.buffer import BufferFull, RedisStreamBuffer

from .helpers import make_event

pytestmark = pytest.mark.redis


def buffer(
    client: redis.Redis, stream: str, consumer: str = "w1", high_watermark: int = 1000
) -> RedisStreamBuffer:
    buf = RedisStreamBuffer(client, stream=stream, consumer=consumer, high_watermark=high_watermark)
    buf.ensure_group()
    return buf


def test_publish_read_ack_round_trip(redis_client: redis.Redis, stream_name: str) -> None:
    buf = buffer(redis_client, stream_name)
    buf.ensure_group()  # idempotent
    events = [make_event("u1"), make_event("u2", properties={"plan": "pro"})]
    buf.publish(events)
    batches = buf.read(count=10, block_ms=100)
    assert [b.events for b in batches] == [events]
    buf.ack([b.id for b in batches])
    assert buf.depth() == 0
    assert buf.read(count=10, block_ms=10) == []


def test_unacked_batch_is_reclaimed_by_another_consumer(
    redis_client: redis.Redis, stream_name: str
) -> None:
    crashed = buffer(redis_client, stream_name, "crashed")
    crashed.publish([make_event("u1")])
    assert len(crashed.read(count=10, block_ms=100)) == 1  # read, never acked
    survivor = buffer(redis_client, stream_name, "survivor")
    assert survivor.reclaim(min_idle_ms=60_000, max_deliveries=5) == []  # not idle long enough
    time.sleep(0.05)
    reclaimed = survivor.reclaim(min_idle_ms=10, max_deliveries=5)
    assert [b.events[0].distinct_id for b in reclaimed] == ["u1"]


def test_repeatedly_failing_batch_goes_to_dead_letter(
    redis_client: redis.Redis, stream_name: str
) -> None:
    buf = buffer(redis_client, stream_name)
    buf.publish([make_event("poison")])
    buf.read(count=1, block_ms=100)
    for _ in range(2):
        time.sleep(0.02)
        assert len(buf.reclaim(min_idle_ms=10, max_deliveries=3)) == 1
    time.sleep(0.02)
    assert buf.reclaim(min_idle_ms=10, max_deliveries=3) == []
    assert buf.depth() == 0
    assert redis_client.xlen(buf.dead_letter_stream) == 1


def test_publish_refuses_above_high_watermark(redis_client: redis.Redis, stream_name: str) -> None:
    buf = buffer(redis_client, stream_name, high_watermark=2)
    buf.publish([make_event("a")])
    buf.publish([make_event("b")])
    with pytest.raises(BufferFull):
        buf.publish([make_event("c")])
