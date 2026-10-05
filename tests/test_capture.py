from __future__ import annotations

import gzip
import json
from collections.abc import Iterator, Sequence

import pytest
from django.test import Client
from rest_framework.test import APIClient

from product_pulse.core.models import Project
from product_pulse.ingestion.buffer import BufferFull
from product_pulse.ingestion.events import CapturedEvent
from product_pulse.ingestion.views import CaptureView, token_cache

pytestmark = pytest.mark.django_db


class ListSink:
    def __init__(self, full: bool = False) -> None:
        self.events: list[CapturedEvent] = []
        self.full = full

    def publish(self, events: Sequence[CapturedEvent]) -> None:
        if self.full:
            raise BufferFull("full")
        self.events.extend(events)


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> Iterator[ListSink]:
    s = ListSink()
    monkeypatch.setattr(CaptureView, "sink_factory", staticmethod(lambda: s))
    token_cache.clear()
    yield s
    token_cache.clear()


def post(body: bytes, headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
    response = Client().post(
        "/capture", data=body, content_type="application/json", headers=headers or {}
    )
    return response.status_code, response.json()


def test_batch_is_buffered_with_the_token_project(project: Project, sink: ListSink) -> None:
    body = {
        "api_key": project.api_token,
        "batch": [{"event": "a", "distinct_id": "u1"}, {"event": "b", "distinct_id": "u1"}],
    }
    status, data = post(json.dumps(body).encode())
    assert status == 200 and data["accepted"] == 2
    assert {e.project_id for e in sink.events} == {project.pk}


def test_gzip_body_and_bearer_token(project: Project, sink: ListSink) -> None:
    body = gzip.compress(json.dumps([{"event": "a", "distinct_id": "u1"}]).encode())
    status, data = post(
        body, headers={"Content-Encoding": "gzip", "Authorization": f"Bearer {project.api_token}"}
    )
    assert status == 200 and data["accepted"] == 1


def test_invalid_events_are_reported_and_valid_ones_kept(project: Project, sink: ListSink) -> None:
    body = {
        "api_key": project.api_token,
        "batch": [{"event": "ok", "distinct_id": "u"}, {"event": "bad", "distinct_id": "null"}],
    }
    status, data = post(json.dumps(body).encode())
    assert status == 200
    assert data["accepted"] == 1
    assert data["rejected"] == [
        {"index": 1, "error": "distinct_id: 'null' is not allowed, it would merge unrelated users"}
    ]


def test_missing_or_unknown_token_is_401(sink: ListSink) -> None:
    assert post(b'{"event": "a", "distinct_id": "u"}')[0] == 401
    assert post(b'{"api_key": "pp_nope", "event": "a", "distinct_id": "u"}')[0] == 401
    assert sink.events == []


def test_malformed_body_is_400(project: Project, sink: ListSink) -> None:
    status, data = post(b"{not json")
    assert status == 400 and "JSON" in str(data["error"])


def test_full_buffer_answers_503_so_sdks_retry(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(CaptureView, "sink_factory", staticmethod(lambda: ListSink(full=True)))
    response = APIClient().post(
        "/capture", {"api_key": project.api_token, "event": "a", "distinct_id": "u"}, format="json"
    )
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
