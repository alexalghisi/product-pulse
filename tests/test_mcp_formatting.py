from __future__ import annotations

from datetime import UTC, datetime, timedelta

from product_pulse.analytics.service import EventSummary
from product_pulse.mcp_server import formatting
from product_pulse.query.runner import TrendLine, TrendResult

START = datetime(2026, 6, 1, tzinfo=UTC)


def trend(buckets: int, lines: int) -> TrendResult:
    return TrendResult(
        interval="hour",
        date_from=START,
        date_to=START + timedelta(hours=buckets),
        buckets=[START + timedelta(hours=i) for i in range(buckets)],
        series=[
            TrendLine(
                event="e",
                math="total",
                breakdown_value=f"v{i}",
                data=[1.0] * buckets,
                total=buckets,
            )
            for i in range(lines)
        ],
    )


def test_small_trend_is_returned_whole() -> None:
    out = formatting.trend(trend(24, 2))
    assert len(out["buckets"]) == 24
    assert all(len(s["data"]) == 24 for s in out["series"])
    assert "notes" not in out


def test_large_trend_keeps_totals_and_says_how_to_get_detail() -> None:
    out = formatting.trend(trend(24 * 30, 25))
    assert "buckets" not in out
    assert len(out["series"]) == formatting.MAX_TREND_LINES
    assert all("data" not in s and s["total"] == 720 for s in out["series"])
    notes = " ".join(out["notes"])
    assert "15 of 25 series omitted" in notes and "larger interval" in notes


def test_event_list_is_capped_with_a_note() -> None:
    items = [EventSummary(name=f"e{i}", count=1000 - i, last_seen=START) for i in range(150)]
    out = formatting.events(items)
    assert len(out["events"]) == 100 and out["total_events"] == 150
    assert "still exist" in out["truncated"]
