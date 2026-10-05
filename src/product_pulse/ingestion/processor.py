"""Turns buffered events into ClickHouse rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from product_pulse.ingestion.events import CapturedEvent
from product_pulse.ingestion.persons import DistinctIdRow


class RowWriter(Protocol):
    def insert_json_rows(self, table: str, rows: Iterable[Mapping[str, Any]]) -> int: ...


class Resolver(Protocol):
    def resolve(self, events: Sequence[CapturedEvent]) -> list[DistinctIdRow]: ...


@dataclass(frozen=True)
class ProcessResult:
    events: int
    duplicates_in_batch: int
    distinct_id_rows: int


class EventProcessor:
    def __init__(self, resolver: Resolver, writer: RowWriter) -> None:
        self.resolver = resolver
        self.writer = writer

    def process(self, events: Sequence[CapturedEvent]) -> ProcessResult:
        unique: dict[tuple[int, str], CapturedEvent] = {}
        for event in events:
            unique.setdefault((event.project_id, event.uuid), event)
        batch = list(unique.values())

        mappings = self.resolver.resolve(batch)
        # Mappings go first, so no query ever sees an event whose distinct_id has no
        # person. A retried batch re-sends identical rows, which ClickHouse collapses.
        if mappings:
            self.writer.insert_json_rows(
                "person_distinct_id", (m.to_clickhouse() for m in mappings)
            )
        self.writer.insert_json_rows("events", (e.to_clickhouse() for e in batch))
        return ProcessResult(
            events=len(batch),
            duplicates_in_batch=len(events) - len(batch),
            distinct_id_rows=len(mappings),
        )
