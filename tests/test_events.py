from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from product_pulse.ingestion.events import CapturedEvent, validate_batch, validate_event

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def ok(raw: object) -> CapturedEvent:
    result = validate_event(raw, project_id=1, now=NOW)
    assert isinstance(result, CapturedEvent), result
    return result


def err(raw: object) -> str:
    result = validate_event(raw, project_id=1, now=NOW)
    assert isinstance(result, str), result
    return result


def test_minimal_event_gets_uuid_and_server_timestamp() -> None:
    event = ok({"event": "signed_up", "distinct_id": "u1"})
    assert event.timestamp == NOW
    assert len(event.uuid) == 36
    assert event.properties == {}


def test_distinct_id_can_come_from_properties_like_browser_sdks_send_it() -> None:
    event = ok(
        {"event": "$pageview", "properties": {"distinct_id": "anon-1", "$browser": "Firefox"}}
    )
    assert event.distinct_id == "anon-1"
    assert event.properties == {"$browser": "Firefox"}


def test_numeric_distinct_id_is_stringified() -> None:
    assert ok({"event": "x", "distinct_id": 42}).distinct_id == "42"


@pytest.mark.parametrize("value", ["null", "undefined", "  ", "anonymous", "0", "[object Object]"])
def test_placeholder_distinct_ids_are_rejected(value: str) -> None:
    assert "distinct_id" in err({"event": "x", "distinct_id": value})


def test_naive_timestamps_are_utc_and_offsets_are_converted() -> None:
    assert ok(
        {"event": "x", "distinct_id": "u", "timestamp": "2026-06-01T10:00:00"}
    ).timestamp == datetime(2026, 6, 1, 10, tzinfo=UTC)
    assert ok(
        {"event": "x", "distinct_id": "u", "timestamp": "2026-06-01T12:00:00+02:00"}
    ).timestamp == datetime(2026, 6, 1, 10, tzinfo=UTC)


def test_far_future_timestamp_is_rejected_but_small_skew_is_fine() -> None:
    soon = (NOW + timedelta(minutes=30)).isoformat()
    later = (NOW + timedelta(hours=3)).isoformat()
    ok({"event": "x", "distinct_id": "u", "timestamp": soon})
    assert "future" in err({"event": "x", "distinct_id": "u", "timestamp": later})


def test_client_uuid_is_kept_for_idempotency() -> None:
    uid = "0b7a4f4e-8a39-4c1e-9a76-3f6f1d1b2c3d"
    assert ok({"event": "x", "distinct_id": "u", "uuid": uid}).uuid == uid
    assert "uuid" in err({"event": "x", "distinct_id": "u", "uuid": "not-a-uuid"})


def test_oversized_properties_and_blank_names_are_rejected() -> None:
    assert "properties" in err(
        {"event": "x", "distinct_id": "u", "properties": {"blob": "a" * 70_000}}
    )
    assert "blank" in err({"event": "   ", "distinct_id": "u"})
    assert "JSON object" in err(["not", "an", "event"])


def test_batch_reports_rejections_by_index_and_keeps_the_rest() -> None:
    accepted, rejected = validate_batch(
        [{"event": "a", "distinct_id": "u"}, {"event": "b"}, {"event": "c", "distinct_id": "u"}],
        project_id=7,
        now=NOW,
    )
    assert [e.event for e in accepted] == ["a", "c"]
    assert all(e.project_id == 7 for e in accepted)
    assert [(r.index, r.error.split(":")[0]) for r in rejected] == [(1, "distinct_id")]


def test_buffer_round_trip_is_lossless() -> None:
    event = ok({"event": "x", "distinct_id": "u", "properties": {"n": 1.5, "tags": ["a"]}})
    assert CapturedEvent.from_buffer(event.to_buffer()) == event
