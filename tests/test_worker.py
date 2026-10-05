from __future__ import annotations

import time
from collections.abc import Sequence

import pytest
import redis

from product_pulse.ingestion.buffer import RedisStreamBuffer
from product_pulse.ingestion.events import CapturedEvent
from product_pulse.ingestion.persons import DistinctIdRow
from product_pulse.ingestion.processor import EventProcessor
from product_pulse.ingestion.worker import Worker

from .fakes import FakeWriter
from .helpers import make_event


class StaticResolver:
    def resolve(self, events: Sequence[CapturedEvent]) -> list[DistinctIdRow]:
        return [
            DistinctIdRow(e.project_id, e.distinct_id, "00000000-0000-0000-0000-000000000001", 0)
            for e in events
        ]


def test_processor_drops_in_batch_duplicates_and_writes_mappings_first() -> None:
    writer = FakeWriter()
    event = make_event("u1")
    result = EventProcessor(StaticResolver(), writer).process([event, event, make_event("u2")])
    assert result.events == 2 and result.duplicates_in_batch == 1
    assert len(writer.tables["events"]) == 2
    assert writer.tables["person_distinct_id"]


@pytest.mark.redis
def test_failed_write_is_not_acked_and_succeeds_on_retry(
    redis_client: redis.Redis, stream_name: str
) -> None:
    source = RedisStreamBuffer(redis_client, stream=stream_name)
    source.ensure_group()
    writer = FakeWriter(fail_times=1)
    worker = Worker(
        source,
        EventProcessor(StaticResolver(), writer),
        block_ms=50,
        reclaim_idle_ms=10,
        reclaim_every_s=0,
    )
    source.publish([make_event("u1"), make_event("u2")])

    assert worker.run_once() == 0
    assert worker.stats.failures == 1
    assert source.depth() == 1  # still there, pending

    time.sleep(0.02)
    assert worker.run_once() == 2
    assert worker.stats.reclaimed == 1
    assert source.depth() == 0
    assert len(writer.tables["events"]) == 2


@pytest.mark.redis
def test_poison_entry_does_not_hold_back_the_rest(
    redis_client: redis.Redis, stream_name: str
) -> None:
    source = RedisStreamBuffer(redis_client, stream=stream_name)
    source.ensure_group()
    writer = FakeWriter(fail_when="poison")
    worker = Worker(
        source,
        EventProcessor(StaticResolver(), writer),
        block_ms=50,
        reclaim_idle_ms=10,
        reclaim_every_s=0,
        max_deliveries=2,
    )
    source.publish([make_event("a")])
    source.publish([make_event("b", "poison")])
    source.publish([make_event("c")])

    assert worker.run_once() == 2
    assert sorted(r["distinct_id"] for r in writer.tables["events"]) == ["a", "c"]
    for _ in range(3):
        time.sleep(0.02)
        worker.run_once()
    assert source.depth() == 0
    assert redis_client.xlen(source.dead_letter_stream) == 1
