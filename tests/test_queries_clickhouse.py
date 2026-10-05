"""Query results against ClickHouse, checked against hand-computed answers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.core.models import Project
from product_pulse.query.models import InsightQuery
from product_pulse.query.runner import FunnelResult, QueryRunner, RetentionResult, TrendResult

from .dataset import load, noise_events, reference_events

pytestmark = [pytest.mark.django_db, pytest.mark.clickhouse]

NOW = datetime(2026, 7, 1, tzinfo=UTC)
JUNE = {"date_from": "2026-06-01", "date_to": "2026-06-30"}
ADAPTER: TypeAdapter[InsightQuery] = TypeAdapter(InsightQuery)


@pytest.fixture
def loaded(clickhouse: ClickHouseClient, project: Project, other_project: Project) -> QueryRunner:
    load(clickhouse, reference_events(project.pk))
    load(clickhouse, noise_events(other_project.pk))
    return QueryRunner(clickhouse)


def run(runner: QueryRunner, project: Project, query: dict[str, Any], now: datetime = NOW) -> Any:
    return runner.run(ADAPTER.validate_python(query), project_id=project.pk, now=now)


def trend(runner: QueryRunner, project: Project, **kw: Any) -> TrendResult:
    query: dict[str, Any] = {"kind": "trend", "date_range": JUNE, **kw}
    result = run(runner, project, query)
    assert isinstance(result, TrendResult)
    return result


def test_daily_totals_fill_empty_buckets(loaded: QueryRunner, project: Project) -> None:
    result = trend(loaded, project, series=[{"event": "$pageview"}])
    line = result.series[0]
    assert len(result.buckets) == 30
    assert line.data[:4] == [4, 2, 1, 0]
    assert line.total == 7


def test_unique_persons_total_is_not_the_sum_of_daily_uniques(
    loaded: QueryRunner, project: Project
) -> None:
    line = trend(loaded, project, series=[{"event": "$pageview", "math": "unique_persons"}]).series[
        0
    ]
    assert line.data[:3] == [2, 2, 1]
    assert line.total == 3  # anon-3 and u3 are one person after $identify


def test_property_math_reads_numbers_and_numeric_strings(
    loaded: QueryRunner, project: Project
) -> None:
    series = [
        {"event": "purchase", "math": "sum", "math_property": "amount"},
        {"event": "purchase", "math": "max", "math_property": "amount"},
    ]
    result = trend(loaded, project, series=series)
    assert [s.total for s in result.series] == [35.5, 20.5]


@pytest.mark.parametrize(
    ("flt", "expected"),
    [
        ({"key": "$browser", "value": "Firefox"}, 2),
        ({"key": "$browser", "operator": "neq", "value": "Firefox"}, 5),
        ({"key": "$browser", "value": ["Firefox", "Safari"]}, 3),
        ({"key": "$browser", "operator": "contains", "value": "fire"}, 2),
        ({"key": "$browser", "operator": "is_not_set"}, 1),
    ],
)
def test_filters(loaded: QueryRunner, project: Project, flt: dict[str, Any], expected: int) -> None:
    assert (
        trend(loaded, project, series=[{"event": "$pageview"}], filters=[flt]).series[0].total
        == expected
    )


def test_breakdown_keeps_top_values_and_groups_the_rest(
    loaded: QueryRunner, project: Project
) -> None:
    result = trend(
        loaded, project, series=[{"event": "$pageview"}], breakdown="$browser", breakdown_limit=2
    )
    assert [(s.breakdown_value, s.total) for s in result.series] == [
        ("Chrome", 3),
        ("Firefox", 2),
        ("$other", 2),
    ]
    result = trend(
        loaded, project, series=[{"event": "$pageview"}], breakdown="$browser", breakdown_limit=10
    )
    assert ("$none", 1) in [(s.breakdown_value, s.total) for s in result.series]


def test_weekly_interval(loaded: QueryRunner, project: Project) -> None:
    result = trend(loaded, project, series=[{"event": "$pageview"}], interval="week")
    assert result.buckets[0] == datetime(2026, 6, 1, tzinfo=UTC)
    assert result.series[0].data[0] == 7


def funnel(runner: QueryRunner, project: Project, **kw: Any) -> FunnelResult:
    steps = [{"event": "signed_up"}, {"event": "project_created"}, {"event": "invited_teammate"}]
    result = run(runner, project, {"kind": "funnel", "steps": steps, "date_range": JUNE, **kw})
    assert isinstance(result, FunnelResult)
    return result


def test_funnel_respects_the_conversion_window(loaded: QueryRunner, project: Project) -> None:
    one_day = funnel(loaded, project, conversion_window=1)
    assert [s.persons for s in one_day.overall] == [3, 2, 1]
    assert one_day.overall[1].conversion_from_start == pytest.approx(0.6667)
    three_days = funnel(loaded, project, conversion_window=3)
    assert [s.persons for s in three_days.overall] == [3, 3, 1]


def test_funnel_breakdown_uses_the_first_step_property(
    loaded: QueryRunner, project: Project
) -> None:
    result = funnel(loaded, project, conversion_window=1, breakdown="plan")
    assert {b.breakdown_value: [s.persons for s in b.steps] for b in result.breakdowns} == {
        "free": [2, 1, 0],
        "pro": [1, 1, 1],
    }


def test_funnel_breakdown_limit_folds_the_rest(loaded: QueryRunner, project: Project) -> None:
    result = funnel(loaded, project, conversion_window=1, breakdown="plan", breakdown_limit=1)
    assert [(b.breakdown_value, b.steps[0].persons) for b in result.breakdowns] == [
        ("free", 2),
        ("$other", 1),
    ]


def test_daily_retention_with_unfinished_periods(loaded: QueryRunner, project: Project) -> None:
    query = {
        "kind": "retention",
        "start_event": {"event": "app_opened"},
        "return_event": {"event": "app_opened"},
        "period": "day",
        "periods": 3,
        "date_range": {"date_from": "2026-06-01", "date_to": "2026-06-02"},
    }
    result = run(loaded, project, query, now=datetime(2026, 6, 4, 12, tzinfo=UTC))
    assert isinstance(result, RetentionResult)
    assert [(str(c.cohort), c.size, c.retained) for c in result.cohorts] == [
        ("2026-06-01", 2, [1, 2, 0]),
        ("2026-06-02", 1, [0, 1, None]),
    ]
    assert result.cohorts[0].rates == [0.5, 1.0, 0.0]


def test_results_never_include_another_project(
    loaded: QueryRunner, project: Project, other_project: Project
) -> None:
    """The other project has the same event names and distinct_ids, plus more of them."""
    assert trend(loaded, project, series=[{"event": "$pageview"}]).series[0].total == 7
    assert trend(loaded, other_project, series=[{"event": "$pageview"}]).series[0].total == 57
    assert [s.persons for s in funnel(loaded, project, conversion_window=1).overall] == [3, 2, 1]
    # A property called project_id is just a property: it can't select another project's rows.
    spoof = [{"key": "project_id", "value": str(other_project.pk)}]
    assert (
        trend(loaded, project, series=[{"event": "$pageview"}], filters=spoof).series[0].total == 0
    )
    # The other project's $identify linked anon-3 with u1 there; here they stay apart.
    uniques = trend(loaded, project, series=[{"event": "$pageview", "math": "unique_persons"}])
    assert uniques.series[0].total == 3


def test_unknown_project_sees_nothing(loaded: QueryRunner) -> None:
    query = ADAPTER.validate_python(
        {"kind": "trend", "series": [{"event": "$pageview"}], "date_range": JUNE}
    )
    result = loaded.run(query, project_id=999_999, now=NOW)
    assert isinstance(result, TrendResult)
    assert result.series[0].total == 0
