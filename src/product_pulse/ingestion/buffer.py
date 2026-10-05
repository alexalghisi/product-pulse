"""The buffer between /capture and the ClickHouse writer: a Redis stream with a consumer group.

Capture appends one stream entry per accepted batch and returns. Workers read entries
through a consumer group and acknowledge them only after ClickHouse has the rows, so a
worker that dies mid-batch leaves the entry pending, and another worker reclaims it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import redis

from product_pulse.ingestion.events import CapturedEvent


class BufferFull(RuntimeError):
    pass


@dataclass(frozen=True)
class BufferedBatch:
    id: str
    events: list[CapturedEvent]


class EventSink(Protocol):
    def publish(self, events: Sequence[CapturedEvent]) -> None: ...


class EventSource(Protocol):
    def read(self, *, count: int, block_ms: int) -> list[BufferedBatch]: ...

    def ack(self, ids: Sequence[str]) -> None: ...

    def reclaim(self, *, min_idle_ms: int, max_deliveries: int) -> list[BufferedBatch]: ...


def _encode(events: Sequence[CapturedEvent]) -> bytes:
    return json.dumps([e.to_buffer() for e in events], separators=(",", ":")).encode()


def _decode(entry_id: bytes | str, fields: dict[bytes, bytes]) -> BufferedBatch:
    raw = json.loads(fields[b"events"])
    return BufferedBatch(
        id=entry_id.decode() if isinstance(entry_id, bytes) else entry_id,
        events=[CapturedEvent.from_buffer(item) for item in raw],
    )


class RedisStreamBuffer:
    def __init__(
        self,
        client: redis.Redis,
        *,
        stream: str,
        group: str = "pulse-writers",
        consumer: str = "writer-1",
        high_watermark: int = 200_000,
    ) -> None:
        self.redis = client
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.high_watermark = high_watermark
        self.dead_letter_stream = f"{stream}:dead"

    # -- producer side ---------------------------------------------------------------

    def publish(self, events: Sequence[CapturedEvent]) -> None:
        if not events:
            return
        if self.depth() >= self.high_watermark:
            raise BufferFull(f"{self.stream} holds {self.high_watermark}+ batches")
        self.redis.xadd(self.stream, {"events": _encode(events)})

    def depth(self) -> int:
        return int(self.redis.xlen(self.stream))

    # -- consumer side ---------------------------------------------------------------

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def read(self, *, count: int, block_ms: int) -> list[BufferedBatch]:
        response = cast(
            list[Any],
            self.redis.xreadgroup(
                self.group, self.consumer, {self.stream: ">"}, count=count, block=block_ms
            ),
        )
        batches: list[BufferedBatch] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                batches.append(_decode(entry_id, fields))
        return batches

    def ack(self, ids: Sequence[str]) -> None:
        if ids:
            pipe = self.redis.pipeline()
            pipe.xack(self.stream, self.group, *ids)
            # Acknowledged entries are no longer needed; deleting keeps the stream short so
            # XLEN is a meaningful backlog measure for backpressure.
            pipe.xdel(self.stream, *ids)
            pipe.execute()

    def reclaim(self, *, min_idle_ms: int, max_deliveries: int) -> list[BufferedBatch]:
        """Take over entries another consumer left pending; dead-letter repeat offenders."""
        pending = cast(
            list[dict[str, Any]],
            self.redis.xpending_range(
                self.stream, self.group, min="-", max="+", count=100, idle=min_idle_ms
            ),
        )
        if not pending:
            return []
        poison = [p["message_id"] for p in pending if p["times_delivered"] >= max_deliveries]
        retry = [p["message_id"] for p in pending if p["times_delivered"] < max_deliveries]
        if poison:
            self._dead_letter(poison)
        if not retry:
            return []
        claimed = cast(
            list[tuple[bytes, dict[bytes, bytes] | None]],
            self.redis.xclaim(self.stream, self.group, self.consumer, min_idle_ms, retry),
        )
        return [_decode(entry_id, fields) for entry_id, fields in claimed if fields]

    def _dead_letter(self, ids: Sequence[bytes | str]) -> None:
        for entry_id in ids:
            entries = cast(
                list[tuple[bytes, dict[bytes, bytes]]],
                self.redis.xrange(self.stream, min=entry_id, max=entry_id),
            )
            pipe = self.redis.pipeline()
            for _, fields in entries:
                dead: dict[Any, Any] = {**fields, b"source_id": entry_id}
                pipe.xadd(self.dead_letter_stream, dead)
            pipe.xack(self.stream, self.group, entry_id)
            pipe.xdel(self.stream, entry_id)
            pipe.execute()
