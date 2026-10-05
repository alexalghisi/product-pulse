"""Capture -> Redis stream -> worker -> ClickHouse, against the real services."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import redis
from rest_framework.test import APIClient

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.core.models import Project
from product_pulse.ingestion.buffer import RedisStreamBuffer
from product_pulse.ingestion.persons import PersonResolver
from product_pulse.ingestion.processor import EventProcessor
from product_pulse.ingestion.views import CaptureView, token_cache
from product_pulse.ingestion.worker import Worker

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.clickhouse, pytest.mark.redis]


@pytest.fixture
def stream(
    redis_client: redis.Redis, stream_name: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[RedisStreamBuffer]:
    buf = RedisStreamBuffer(redis_client, stream=stream_name)
    buf.ensure_group()
    monkeypatch.setattr(CaptureView, "sink_factory", staticmethod(lambda: buf))
    token_cache.clear()
    yield buf


def capture(project: Project, events: list[dict[str, object]]) -> None:
    response = APIClient().post(
        "/capture", {"api_key": project.api_token, "batch": events}, format="json"
    )
    assert response.status_code == 200, response.content


def drain(worker: Worker) -> None:
    while worker.run_once():
        pass


def test_events_land_in_clickhouse_once_even_when_retried(
    project: Project, stream: RedisStreamBuffer, clickhouse: ClickHouseClient
) -> None:
    batch: list[dict[str, object]] = [
        {
            "event": "$pageview",
            "distinct_id": "anon-1",
            "uuid": f"00000000-0000-4000-8000-00000000000{i}",
            "timestamp": f"2026-06-01T10:0{i}:00Z",
            "properties": {"$browser": "Firefox"},
        }
        for i in range(5)
    ]
    capture(project, batch)
    capture(project, batch)  # an SDK retry after a timeout
    worker = Worker(stream, EventProcessor(PersonResolver(), clickhouse), block_ms=50)
    drain(worker)

    rows = clickhouse.query(
        "SELECT count() AS n FROM events FINAL WHERE project_id = {p:UInt64}", {"p": project.pk}
    )
    assert rows == [{"n": 5}]
    props = clickhouse.query(
        "SELECT any(properties) AS p FROM events WHERE project_id = {p:UInt64}", {"p": project.pk}
    )
    assert json.loads(props[0]["p"]) == {"$browser": "Firefox"}


def test_identify_makes_earlier_anonymous_events_count_for_the_user(
    project: Project, stream: RedisStreamBuffer, clickhouse: ClickHouseClient
) -> None:
    worker = Worker(stream, EventProcessor(PersonResolver(), clickhouse), block_ms=50)
    capture(
        project,
        [{"event": "$pageview", "distinct_id": "anon-1", "timestamp": "2026-06-01T10:00:00Z"}],
    )
    drain(worker)
    capture(
        project,
        [
            {"event": "signed_up", "distinct_id": "user-1", "timestamp": "2026-06-01T10:05:00Z"},
            {
                "event": "$identify",
                "distinct_id": "user-1",
                "timestamp": "2026-06-01T10:05:01Z",
                "properties": {"$anon_distinct_id": "anon-1"},
            },
        ],
    )
    drain(worker)

    rows = clickhouse.query(
        """
        SELECT uniqExact(pdi.person_id) AS persons
        FROM events AS e FINAL
        INNER JOIN (
            SELECT distinct_id, argMax(person_id, version) AS person_id
            FROM person_distinct_id WHERE project_id = {p:UInt64} GROUP BY distinct_id
        ) AS pdi ON e.distinct_id = pdi.distinct_id
        WHERE e.project_id = {p:UInt64}
        """,
        {"p": project.pk},
    )
    assert rows == [{"persons": 1}]
