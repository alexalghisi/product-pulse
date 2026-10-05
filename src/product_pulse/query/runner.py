"""Runs compiled queries and shapes the rows into results for people and agents."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from product_pulse.clickhouse.client import QueryExecutor
from product_pulse.query import dates
from product_pulse.query.compiler import (
    MAX_BUCKETS,
    NONE,
    OTHER,
    compile_funnel,
    compile_retention,
    compile_trend_series,
)
from product_pulse.query.models import FunnelQuery, InsightQuery, RetentionQuery, TrendQuery


class TrendLine(BaseModel):
    event: str
    math: str
    breakdown_value: str | None
    data: list[float]
    total: float


class TrendResult(BaseModel):
    kind: Literal["trend"] = "trend"
    interval: str
    date_from: datetime
    date_to: datetime
    buckets: list[datetime]
    series: list[TrendLine]


class FunnelStepResult(BaseModel):
    event: str
    persons: int
    conversion_from_start: float
    conversion_from_previous: float


class FunnelBreakdown(BaseModel):
    breakdown_value: str | None
    steps: list[FunnelStepResult]


class FunnelResult(BaseModel):
    kind: Literal["funnel"] = "funnel"
    date_from: datetime
    date_to: datetime
    conversion_window: str
    overall: list[FunnelStepResult]
    breakdowns: list[FunnelBreakdown]


class RetentionCohort(BaseModel):
    cohort: date
    size: int
    # retained[k-1] = persons who came back in period k; None when period k hasn't started.
    retained: list[int | None]
    rates: list[float | None]


class RetentionResult(BaseModel):
    kind: Literal["retention"] = "retention"
    period: str
    cohorts: list[RetentionCohort]


QueryResult = TrendResult | FunnelResult | RetentionResult


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _number(value: object) -> float:
    if value is None:
        return 0.0
    number = float(value)  # type: ignore[arg-type]
    return 0.0 if number != number else number  # avg over no rows is NaN


class QueryRunner:
    def __init__(self, executor: QueryExecutor) -> None:
        self.executor = executor

    def run(
        self, query: InsightQuery, *, project_id: int, now: datetime | None = None
    ) -> QueryResult:
        now = now or datetime.now(UTC)
        if isinstance(query, TrendQuery):
            return self.trend(query, project_id=project_id, now=now)
        if isinstance(query, FunnelQuery):
            return self.funnel(query, project_id=project_id, now=now)
        return self.retention(query, project_id=project_id, now=now)

    def trend(self, query: TrendQuery, *, project_id: int, now: datetime) -> TrendResult:
        resolved = dates.resolve(query.date_range, now)
        buckets = dates.buckets(resolved, query.interval, MAX_BUCKETS)
        index = {int(b.timestamp()): i for i, b in enumerate(buckets)}
        lines: list[TrendLine] = []
        for i, series in enumerate(query.series):
            compiled = compile_trend_series(query, i, project_id=project_id, now=now)
            rows = self.executor.query(compiled.sql, compiled.params)
            data: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(buckets))
            totals: dict[str, float] = {}
            for row in rows:
                value = str(row["breakdown_value"]) or NONE
                if row["is_total"]:
                    totals[value] = _number(row["value"])
                elif (position := index.get(int(row["bucket"]))) is not None:
                    data[value][position] = _number(row["value"])
            ordered = sorted(totals, key=lambda v: (v == OTHER, -totals[v], v))
            for value in ordered:
                lines.append(
                    TrendLine(
                        event=series.event,
                        math=series.math,
                        breakdown_value=value if query.breakdown else None,
                        data=data[value],
                        total=totals[value],
                    )
                )
            if not ordered:
                lines.append(
                    TrendLine(
                        event=series.event,
                        math=series.math,
                        breakdown_value=None,
                        data=[0.0] * len(buckets),
                        total=0.0,
                    )
                )
        return TrendResult(
            interval=query.interval,
            date_from=resolved.start,
            date_to=resolved.end,
            buckets=buckets,
            series=lines,
        )

    def funnel(self, query: FunnelQuery, *, project_id: int, now: datetime) -> FunnelResult:
        resolved = dates.resolve(query.date_range, now)
        compiled = compile_funnel(query, project_id=project_id, now=now)
        rows = self.executor.query(compiled.sql, compiled.params)
        step_count = len(query.steps)
        # reached[value][k] = persons whose furthest step is exactly k+1
        reached: dict[str, list[int]] = defaultdict(lambda: [0] * step_count)
        for row in rows:
            value = str(row["breakdown_value"]) or NONE
            reached[value][int(row["level"]) - 1] += int(row["persons"])

        def cumulative(exact: list[int]) -> list[int]:
            return [sum(exact[k:]) for k in range(step_count)]

        def shape(counts: list[int]) -> list[FunnelStepResult]:
            return [
                FunnelStepResult(
                    event=step.event,
                    persons=counts[k],
                    conversion_from_start=_rate(counts[k], counts[0]),
                    conversion_from_previous=_rate(counts[k], counts[k - 1]) if k else 1.0,
                )
                for k, step in enumerate(query.steps)
            ]

        per_value = {value: cumulative(exact) for value, exact in reached.items()}
        overall = [sum(c[k] for c in per_value.values()) for k in range(step_count)]
        breakdowns: list[FunnelBreakdown] = []
        if query.breakdown:
            ranked = sorted(per_value, key=lambda v: (-per_value[v][0], v))
            kept, rest = ranked[: query.breakdown_limit], ranked[query.breakdown_limit :]
            # Each person has exactly one breakdown value, so folding counts is exact.
            for value in kept:
                breakdowns.append(
                    FunnelBreakdown(breakdown_value=value, steps=shape(per_value[value]))
                )
            if rest:
                folded = [sum(per_value[v][k] for v in rest) for k in range(step_count)]
                breakdowns.append(FunnelBreakdown(breakdown_value=OTHER, steps=shape(folded)))
        return FunnelResult(
            date_from=resolved.start,
            date_to=resolved.end,
            conversion_window=f"{query.conversion_window} {query.conversion_window_unit}",
            overall=shape(overall),
            breakdowns=breakdowns,
        )

    def retention(
        self, query: RetentionQuery, *, project_id: int, now: datetime
    ) -> RetentionResult:
        compiled = compile_retention(query, project_id=project_id, now=now)
        rows = self.executor.query(compiled.sql, compiled.params)
        sizes: dict[date, int] = {}
        returned: dict[tuple[date, int], int] = {}
        for row in rows:
            cohort = date.fromisoformat(str(row["cohort"]))
            if int(row["offset"]) == 0:
                sizes[cohort] = int(row["persons"])
            else:
                returned[(cohort, int(row["offset"]))] = int(row["persons"])

        days = 1 if query.period == "day" else 7
        today = now.astimezone(UTC).date()
        cohorts: list[RetentionCohort] = []
        for cohort in sorted(sizes):
            retained: list[int | None] = []
            for k in range(1, query.periods + 1):
                period_start = cohort + timedelta(days=days * k)
                retained.append(returned.get((cohort, k), 0) if period_start <= today else None)
            cohorts.append(
                RetentionCohort(
                    cohort=cohort,
                    size=sizes[cohort],
                    retained=retained,
                    rates=[None if r is None else _rate(r, sizes[cohort]) for r in retained],
                )
            )
        return RetentionResult(period=query.period, cohorts=cohorts)
