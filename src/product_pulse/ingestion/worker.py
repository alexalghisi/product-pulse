"""The writer loop: buffer -> persons -> ClickHouse, acknowledging only what was written."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from product_pulse.ingestion.buffer import BufferedBatch, EventSource
from product_pulse.ingestion.processor import EventProcessor

logger = logging.getLogger(__name__)


@dataclass
class WorkerStats:
    batches: int = 0
    events: int = 0
    failures: int = 0
    reclaimed: int = 0
    last_error: str | None = None
    timings: list[float] = field(default_factory=list)


class Worker:
    def __init__(
        self,
        source: EventSource,
        processor: EventProcessor,
        *,
        read_count: int = 50,
        block_ms: int = 1000,
        reclaim_idle_ms: int = 60_000,
        max_deliveries: int = 5,
        reclaim_every_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.processor = processor
        self.read_count = read_count
        self.block_ms = block_ms
        self.reclaim_idle_ms = reclaim_idle_ms
        self.max_deliveries = max_deliveries
        self.reclaim_every_s = reclaim_every_s
        self.clock = clock
        self.stats = WorkerStats()
        self._last_reclaim = float("-inf")

    def run_once(self) -> int:
        """Process one read's worth of batches. Returns the number of events written."""
        batches: list[BufferedBatch] = []
        if self.clock() - self._last_reclaim >= self.reclaim_every_s:
            self._last_reclaim = self.clock()
            reclaimed = self.source.reclaim(
                min_idle_ms=self.reclaim_idle_ms, max_deliveries=self.max_deliveries
            )
            self.stats.reclaimed += len(reclaimed)
            batches.extend(reclaimed)
        batches.extend(self.source.read(count=self.read_count, block_ms=self.block_ms))
        if not batches:
            return 0

        started = self.clock()
        written = self._write(batches)
        if written is None and len(batches) > 1:
            # Isolate the failure: write entries one by one so a single poison entry
            # doesn't hold back the good ones read alongside it.
            written = sum(self._write([batch]) or 0 for batch in batches)
        self.stats.timings.append(self.clock() - started)
        return written or 0

    def _write(self, batches: list[BufferedBatch]) -> int | None:
        events = [event for batch in batches for event in batch.events]
        try:
            result = self.processor.process(events)
        except Exception as exc:
            # The entries stay pending. They are retried after reclaim_idle_ms and
            # dead-lettered after max_deliveries, so a poison entry can't wedge the loop.
            self.stats.failures += 1
            self.stats.last_error = str(exc)
            logger.warning("failed to write %d events, will retry: %s", len(events), exc)
            return None
        self.source.ack([batch.id for batch in batches])
        self.stats.batches += len(batches)
        self.stats.events += result.events
        return result.events

    def run_forever(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        while not should_stop():
            self.run_once()
