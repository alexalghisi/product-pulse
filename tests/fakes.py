"""Fakes for the ClickHouse writer, with fault injection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class FakeWriter:
    def __init__(self, *, fail_times: int = 0, fail_when: str | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {"events": [], "person_distinct_id": []}
        self.fail_times = fail_times
        # Fails every insert whose rows contain this event name: a poison batch.
        self.fail_when = fail_when
        self.calls = 0

    def insert_json_rows(self, table: str, rows: Iterable[Mapping[str, Any]]) -> int:
        self.calls += 1
        materialized = [dict(row) for row in rows]
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("injected: ClickHouse unavailable")
        if self.fail_when and any(r.get("event") == self.fail_when for r in materialized):
            raise ValueError(f"injected: cannot insert {self.fail_when}")
        self.tables[table].extend(materialized)
        return len(materialized)
