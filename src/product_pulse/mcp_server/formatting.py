"""Shapes results for a model's context window.

A 90-day daily trend broken down by 50 values is 4,500 numbers; pasted into a context
window it costs a lot and helps little. Results are trimmed to a budget, and whenever
something is left out the response says what, and how to ask for it.
"""

from __future__ import annotations

from typing import Any

from product_pulse.analytics.service import EventSummary, PropertySummary
from product_pulse.query.runner import FunnelResult, RetentionResult, TrendResult

MAX_EVENTS = 100
MAX_PROPERTIES = 60
MAX_TREND_POINTS = 400
MAX_TREND_LINES = 10
MAX_FUNNEL_BREAKDOWNS = 10
MAX_COHORTS = 26


def _date(value: Any) -> str:
    text: str = value.isoformat()
    return text[:10] if text[11:19] == "00:00:00" else text[:16]


def events(items: list[EventSummary]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "events": [
            {"name": e.name, "count": e.count, "last_seen": _date(e.last_seen)}
            for e in items[:MAX_EVENTS]
        ],
        "total_events": len(items),
    }
    if len(items) > MAX_EVENTS:
        out["truncated"] = (
            f"Showing the {MAX_EVENTS} most frequent of {len(items)} event names. "
            "Names not listed still exist and can be queried."
        )
    return out


def properties(event: str, items: list[PropertySummary]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "event": event,
        "properties": [p.model_dump() for p in items[:MAX_PROPERTIES]],
    }
    if len(items) > MAX_PROPERTIES:
        out["truncated"] = (
            f"Showing {MAX_PROPERTIES} of {len(items)} properties, most common first."
        )
    return out


def trend(result: TrendResult) -> dict[str, Any]:
    lines = result.series
    notes: list[str] = []
    if len(lines) > MAX_TREND_LINES:
        notes.append(
            f"{len(lines) - MAX_TREND_LINES} of {len(lines)} series omitted; lower breakdown_limit "
            "or filter to the values you need."
        )
        lines = lines[:MAX_TREND_LINES]
    include_points = len(result.buckets) * len(lines) <= MAX_TREND_POINTS
    if not include_points:
        notes.append(
            f"Per-{result.interval} values omitted ({len(result.buckets)} buckets x {len(lines)} "
            "series); totals are exact. Use a larger interval or shorter range to see the shape."
        )
    out: dict[str, Any] = {
        "kind": "trend",
        "date_from": _date(result.date_from),
        "date_to": _date(result.date_to),
        "interval": result.interval,
        "series": [],
    }
    if include_points:
        out["buckets"] = [_date(b) for b in result.buckets]
    for line in lines:
        item: dict[str, Any] = {"event": line.event, "math": line.math, "total": line.total}
        if line.breakdown_value is not None:
            item["breakdown_value"] = line.breakdown_value
        if include_points:
            item["data"] = line.data
        out["series"].append(item)
    if any(line.math == "unique_persons" for line in lines):
        notes.append("For unique_persons, 'total' counts each person once over the whole range.")
    if notes:
        out["notes"] = notes
    return out


def funnel(result: FunnelResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "funnel",
        "date_from": _date(result.date_from),
        "date_to": _date(result.date_to),
        "conversion_window": result.conversion_window,
        "overall": [s.model_dump() for s in result.overall],
    }
    breakdowns = result.breakdowns
    if breakdowns:
        out["breakdowns"] = [
            {"breakdown_value": b.breakdown_value, "steps": [s.model_dump() for s in b.steps]}
            for b in breakdowns[:MAX_FUNNEL_BREAKDOWNS]
        ]
        if len(breakdowns) > MAX_FUNNEL_BREAKDOWNS:
            out["truncated"] = f"Showing {MAX_FUNNEL_BREAKDOWNS} of {len(breakdowns)} values."
    return out


def retention(result: RetentionResult) -> dict[str, Any]:
    cohorts = result.cohorts
    out: dict[str, Any] = {"kind": "retention", "period": result.period}
    if len(cohorts) > MAX_COHORTS:
        out["truncated"] = f"Showing the latest {MAX_COHORTS} of {len(cohorts)} cohorts."
        cohorts = cohorts[-MAX_COHORTS:]
    out["cohorts"] = [
        {"cohort": str(c.cohort), "size": c.size, "retained": c.retained, "rates": c.rates}
        for c in cohorts
    ]
    out["notes"] = [
        f"retained[k-1] is persons active in {result.period} k after their cohort {result.period}; "
        "null means that period hasn't started yet."
    ]
    return out
