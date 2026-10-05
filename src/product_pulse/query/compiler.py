"""Compiles typed queries to parameterized ClickHouse SQL.

Two rules hold for everything in this module, and the tests check both:

1. No value from a query reaches the SQL text. Event names, property keys, filter
   values, limits and dates are all bound with `Params.add`, which returns a
   `{name:Type}` placeholder. The SQL text is built only from constants in this file.
2. Tables are named in exactly one place, `Scope`, and every read it produces carries
   `project_id = {project_id:UInt64}`. The project id is an argument of the compile
   functions supplied by the caller's authentication, never a field of the query.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from product_pulse.clickhouse.client import ParamValue
from product_pulse.query import dates
from product_pulse.query.models import (
    FunnelQuery,
    PropertyFilter,
    RetentionQuery,
    TrendQuery,
    TrendSeries,
)

MAX_BUCKETS = 1_000
OTHER = "$other"  # breakdown values beyond the limit
NONE = "$none"  # events without the breakdown property


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: dict[str, ParamValue]


@dataclass
class Params:
    values: dict[str, ParamValue] = field(default_factory=dict)

    def add(self, value: ParamValue, ch_type: str) -> str:
        name = f"p{len(self.values)}"
        self.values[name] = value
        return "{" + name + ":" + ch_type + "}"


class Scope:
    """The only code that names a table. Every read is filtered to one project."""

    PROJECT = "{project_id:UInt64}"

    def __init__(self, project_id: int, params: Params) -> None:
        if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
            raise ValueError("project_id must be a positive integer")
        params.values["project_id"] = project_id
        self.params = params

    def persons(self) -> str:
        return (
            "SELECT distinct_id, argMax(person_id, version) AS person_id "
            f"FROM person_distinct_id WHERE project_id = {self.PROJECT} GROUP BY distinct_id"
        )

    def events(
        self, *, start: datetime, end: datetime, event_names: Sequence[str], with_persons: bool
    ) -> str:
        columns = "e.event AS event, e.timestamp AS timestamp, e.properties AS properties"
        join = ""
        if with_persons:
            columns += ", pdi.person_id AS person_id"
            join = f" INNER JOIN ({self.persons()}) AS pdi ON e.distinct_id = pdi.distinct_id"
        p = self.params
        return (
            f"SELECT {columns} FROM events AS e FINAL{join}"
            f" WHERE e.project_id = {self.PROJECT}"
            f" AND e.timestamp >= {p.add(start, "DateTime64(3, 'UTC')")}"
            f" AND e.timestamp < {p.add(end, "DateTime64(3, 'UTC')")}"
            f" AND e.event IN {p.add(sorted(set(event_names)), 'Array(String)')}"
        )


# -- property expressions --------------------------------------------------------------


def _text(value: str | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def property_text(params: Params, key: str) -> str:
    return f"JSONExtractString(properties, {params.add(key, 'String')})"


def _filter(params: Params, f: PropertyFilter) -> str:
    key = params.add(f.key, "String")
    text = f"JSONExtractString(properties, {key})"
    number = f"JSONExtractFloat(properties, {key})"
    value = f.value
    match f.operator:
        case "is_set":
            return f"JSONHas(properties, {key})"
        case "is_not_set":
            return f"NOT JSONHas(properties, {key})"
        case "eq" | "neq":
            if isinstance(value, list):
                expr = f"{text} IN {params.add(value, 'Array(String)')}"
            else:
                assert value is not None
                expr = f"{text} = {params.add(_text(value), 'String')}"
            return expr if f.operator == "eq" else f"NOT ({expr})"
        case "contains" | "not_contains":
            assert isinstance(value, str)
            expr = f"positionCaseInsensitiveUTF8({text}, {params.add(value, 'String')}) > 0"
            return expr if f.operator == "contains" else f"NOT ({expr})"
        case "gt" | "gte" | "lt" | "lte":
            assert isinstance(value, int | float)
            op = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[f.operator]
            bound = params.add(float(value), "Float64")
            return f"JSONHas(properties, {key}) AND {number} {op} {bound}"
    raise AssertionError(f"unhandled operator {f.operator}")


def filters_sql(params: Params, filters: Sequence[PropertyFilter]) -> str:
    if not filters:
        return "1"
    return " AND ".join(f"({_filter(params, f)})" for f in filters)


# -- trends ------------------------------------------------------------------------------

_BUCKET = {
    "hour": "toStartOfHour(timestamp)",
    "day": "toStartOfDay(timestamp)",
    "week": "toDateTime(toMonday(timestamp), 'UTC')",
    "month": "toDateTime(toStartOfMonth(timestamp), 'UTC')",
}


def _value_expr(params: Params, series: TrendSeries) -> str:
    if series.math == "total":
        return "count()"
    if series.math == "unique_persons":
        return "uniqExact(person_id)"
    assert series.math_property is not None
    key = params.add(series.math_property, "String")
    func = {"sum": "sumIf", "avg": "avgIf", "min": "minIf", "max": "maxIf"}[series.math]
    return f"{func}(JSONExtractFloat(properties, {key}), JSONHas(properties, {key}))"


def compile_trend_series(
    query: TrendQuery, series_index: int, *, project_id: int, now: datetime
) -> CompiledQuery:
    """One series of a trend: a row per (breakdown value, bucket) plus a total per value.

    Totals come from a second grouping set rather than from adding up buckets, because a
    sum of daily unique persons is not the number of unique persons over the range.
    """
    series = query.series[series_index]
    resolved = dates.resolve(query.date_range, now)
    dates.buckets(resolved, query.interval, MAX_BUCKETS)  # validates the bucket count

    params = Params()
    scope = Scope(project_id, params)
    source = scope.events(
        start=resolved.start,
        end=resolved.end,
        event_names=[series.event],
        with_persons=series.math == "unique_persons",
    )
    where = (
        f"event = {params.add(series.event, 'String')}"
        f" AND {filters_sql(params, series.filters)}"
        f" AND {filters_sql(params, query.filters)}"
    )
    value = _value_expr(params, series)
    bucket = f"toUnixTimestamp({_BUCKET[query.interval]})"

    if query.breakdown is None:
        breakdown = "''"
        with_clause = f"WITH base AS ({source})"
    else:
        raw = property_text(params, query.breakdown)
        limit = params.add(query.breakdown_limit, "UInt32")
        other = params.add(OTHER, "String")
        with_clause = (
            f"WITH base AS ({source}),"
            f" top AS (SELECT {raw} AS value FROM base WHERE {where}"
            f" GROUP BY value ORDER BY {value} DESC, value LIMIT {limit})"
        )
        breakdown = f"if({raw} IN (SELECT value FROM top), {raw}, {other})"

    sql = (
        f"{with_clause}"
        f" SELECT {breakdown} AS breakdown_value, {bucket} AS bucket,"
        f" {value} AS value, grouping(bucket) AS is_total"
        f" FROM base WHERE {where}"
        " GROUP BY GROUPING SETS ((breakdown_value, bucket), (breakdown_value))"
        " ORDER BY is_total, breakdown_value, bucket"
    )
    return CompiledQuery(sql=sql, params=params.values)


# -- funnels ------------------------------------------------------------------------------


def compile_funnel(query: FunnelQuery, *, project_id: int, now: datetime) -> CompiledQuery:
    """Persons per (breakdown value, furthest step reached), using windowFunnel.

    Step 1 must fall inside the date range; later steps may land up to the conversion
    window after the range ends, otherwise people who started on the last day could
    never convert.
    """
    resolved = dates.resolve(query.date_range, now)
    window = timedelta(seconds=query.window_seconds())
    params = Params()
    scope = Scope(project_id, params)
    source = scope.events(
        start=resolved.start,
        end=resolved.end + window,
        event_names=[s.event for s in query.steps],
        with_persons=True,
    )
    range_end = params.add(resolved.end, "DateTime64(3, 'UTC')")
    conditions: list[str] = []
    for index, step in enumerate(query.steps):
        condition = (
            f"event = {params.add(step.event, 'String')} AND {filters_sql(params, step.filters)}"
        )
        if index == 0:
            condition += f" AND timestamp < {range_end}"
        conditions.append(f"({condition})")

    breakdown = (
        f"argMinIf({property_text(params, query.breakdown)}, timestamp, {conditions[0]})"
        if query.breakdown
        else "''"
    )
    window_param = params.add(query.window_seconds(), "UInt64")
    sql = (
        "SELECT breakdown_value, level, count() AS persons FROM ("
        f" SELECT person_id,"
        f" windowFunnel({window_param})(toDateTime(timestamp), {', '.join(conditions)}) AS level,"
        f" {breakdown} AS breakdown_value"
        f" FROM ({source}) WHERE {filters_sql(params, query.filters)}"
        " GROUP BY person_id"
        ") WHERE level > 0 GROUP BY breakdown_value, level ORDER BY breakdown_value, level"
    )
    return CompiledQuery(sql=sql, params=params.values)


# -- retention ------------------------------------------------------------------------------


def compile_retention(query: RetentionQuery, *, project_id: int, now: datetime) -> CompiledQuery:
    """Cohort sizes (offset 0) and returning persons per cohort and offset.

    A person's cohort is the first period in the range in which they did the start event.
    They count as retained in period k if they did the return event in that period.
    """
    resolved = dates.resolve(query.date_range, now)
    days_per_period = 1 if query.period == "day" else 7
    horizon = timedelta(days=days_per_period * (query.periods + 1))
    params = Params()
    scope = Scope(project_id, params)
    period_of = "toDate(timestamp)" if query.period == "day" else "toMonday(timestamp)"

    start_source = scope.events(
        start=resolved.start,
        end=resolved.end,
        event_names=[query.start_event.event],
        with_persons=True,
    )
    return_source = scope.events(
        start=resolved.start,
        end=resolved.end + horizon,
        event_names=[query.return_event.event],
        with_persons=True,
    )
    global_filters = filters_sql(params, query.filters)
    start_where = (
        f"event = {params.add(query.start_event.event, 'String')}"
        f" AND {filters_sql(params, query.start_event.filters)} AND {global_filters}"
    )
    return_where = (
        f"event = {params.add(query.return_event.event, 'String')}"
        f" AND {filters_sql(params, query.return_event.filters)} AND {global_filters}"
    )
    divisor = params.add(days_per_period, "UInt8")
    max_offset = params.add(query.periods, "UInt8")
    sql = (
        f"WITH starts AS (SELECT person_id, min({period_of}) AS cohort"
        f" FROM ({start_source}) WHERE {start_where} GROUP BY person_id),"
        f" returns AS (SELECT DISTINCT person_id, {period_of} AS period"
        f" FROM ({return_source}) WHERE {return_where})"
        " SELECT toString(cohort) AS cohort, 0 AS offset, count() AS persons"
        " FROM starts GROUP BY cohort"
        " UNION ALL"
        " SELECT toString(s.cohort) AS cohort,"
        f" intDiv(dateDiff('day', s.cohort, r.period), {divisor}) AS offset,"
        " uniqExact(s.person_id) AS persons"
        " FROM starts AS s INNER JOIN returns AS r ON s.person_id = r.person_id"
        f" WHERE r.period > s.cohort AND offset <= {max_offset}"
        " GROUP BY cohort, offset"
    )
    return CompiledQuery(sql=sql, params=params.values)
