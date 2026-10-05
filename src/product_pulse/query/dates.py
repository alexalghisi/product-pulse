"""Resolving date ranges and producing the time buckets results are reported in. UTC only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from product_pulse.query.models import DateRange

Interval = Literal["hour", "day", "week", "month"]

_RELATIVE = re.compile(r"^-(\d{1,4})([hdwmy])$")


@dataclass(frozen=True)
class ResolvedRange:
    start: datetime  # inclusive
    end: datetime  # exclusive

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(
                f"date range is empty: {self.start:%Y-%m-%d %H:%M} is not before "
                f"{self.end:%Y-%m-%d %H:%M}"
            )


def start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _add_months(moment: datetime, months: int) -> datetime:
    index = moment.year * 12 + (moment.month - 1) + months
    year, month = divmod(index, 12)
    return moment.replace(year=year, month=month + 1, day=1)


def _parse_absolute(value: str) -> tuple[datetime, bool]:
    """Return the moment and whether the value was a bare date."""
    if len(value) == 10:
        day = date.fromisoformat(value)
        return datetime(day.year, day.month, day.day, tzinfo=UTC), True
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC), False


def _relative(amount: int, unit: str, now: datetime) -> datetime:
    if unit == "h":
        return now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=amount)
    if unit == "d":
        return start_of_day(now) - timedelta(days=amount)
    if unit == "w":
        return start_of_day(now) - timedelta(weeks=amount)
    months = amount * (12 if unit == "y" else 1)
    return start_of_day(_add_months(now, -months)).replace(day=min(now.day, 28))


def resolve(date_range: DateRange, now: datetime) -> ResolvedRange:
    now = now.astimezone(UTC)

    def point(value: str, *, is_end: bool) -> datetime:
        if match := _RELATIVE.match(value):
            return _relative(int(match.group(1)), match.group(2), now)
        moment, bare_date = _parse_absolute(value)
        # A bare end date is inclusive: "to 2026-06-30" means through the end of that day.
        return moment + timedelta(days=1) if is_end and bare_date else moment

    start = point(date_range.date_from, is_end=False)
    end = now if date_range.date_to is None else point(date_range.date_to, is_end=True)
    return ResolvedRange(start=start, end=end)


def floor(moment: datetime, interval: Interval) -> datetime:
    if interval == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    day = start_of_day(moment)
    if interval == "day":
        return day
    if interval == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def step(moment: datetime, interval: Interval) -> datetime:
    if interval == "hour":
        return moment + timedelta(hours=1)
    if interval == "day":
        return moment + timedelta(days=1)
    if interval == "week":
        return moment + timedelta(weeks=1)
    return _add_months(moment, 1)


def buckets(resolved: ResolvedRange, interval: Interval, limit: int) -> list[datetime]:
    out: list[datetime] = []
    current = floor(resolved.start, interval)
    while current < resolved.end:
        out.append(current)
        if len(out) > limit:
            raise ValueError(
                f"{len(out)}+ {interval} buckets is too many (limit {limit}); "
                "use a larger interval or a shorter date range"
            )
        current = step(current, interval)
    return out
