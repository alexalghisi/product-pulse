from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from product_pulse.query import dates
from product_pulse.query.models import DateRange

NOW = datetime(2026, 6, 17, 15, 42, tzinfo=UTC)  # a Wednesday


def resolve(date_from: str, date_to: str | None = None) -> tuple[datetime, datetime]:
    r = dates.resolve(DateRange(date_from=date_from, date_to=date_to), NOW)
    return r.start, r.end


def test_relative_ranges_start_at_bucket_boundaries_and_end_now() -> None:
    assert resolve("-7d") == (datetime(2026, 6, 10, tzinfo=UTC), NOW)
    assert resolve("-24h")[0] == datetime(2026, 6, 16, 15, tzinfo=UTC)
    assert resolve("-2w")[0] == datetime(2026, 6, 3, tzinfo=UTC)
    assert resolve("-3m")[0] == datetime(2026, 3, 17, tzinfo=UTC)


def test_absolute_end_date_is_inclusive() -> None:
    assert resolve("2026-06-01", "2026-06-30") == (
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 7, 1, tzinfo=UTC),
    )


def test_empty_range_is_an_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        resolve("2026-06-10", "2026-06-01")


@pytest.mark.parametrize("bad", ["last week", "7d", "-7x", "2026/06/01", "'; DROP TABLE events"])
def test_unrecognised_dates_are_rejected_with_a_hint(bad: str) -> None:
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        DateRange(date_from=bad)


def test_weekly_buckets_start_on_monday() -> None:
    r = dates.resolve(DateRange(date_from="2026-06-03", date_to="2026-06-16"), NOW)
    assert [b.day for b in dates.buckets(r, "week", 100)] == [1, 8, 15]


def test_monthly_buckets_cross_year_end() -> None:
    r = dates.resolve(DateRange(date_from="2025-11-15", date_to="2026-02-01"), NOW)
    assert [(b.year, b.month) for b in dates.buckets(r, "month", 100)] == [
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]


def test_too_many_buckets_asks_for_a_larger_interval() -> None:
    r = dates.resolve(DateRange(date_from="-90d"), NOW)
    with pytest.raises(ValueError, match="larger interval"):
        dates.buckets(r, "hour", 1000)
