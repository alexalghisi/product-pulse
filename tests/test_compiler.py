"""Compiler guarantees that don't need a database: no interpolation, every read scoped."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from product_pulse.query.compiler import (
    CompiledQuery,
    compile_event_summary,
    compile_funnel,
    compile_property_summary,
    compile_retention,
    compile_trend_series,
)
from product_pulse.query.models import FunnelQuery, InsightQuery, RetentionQuery, TrendQuery

NOW = datetime(2026, 6, 30, tzinfo=UTC)
EVIL = "x') OR 1=1 UNION SELECT * FROM system.users --{p0:String}\\'"

ADAPTER: TypeAdapter[InsightQuery] = TypeAdapter(InsightQuery)


def evil_filters() -> list[dict[str, object]]:
    return [
        {"key": EVIL, "operator": "eq", "value": EVIL},
        {"key": EVIL, "operator": "eq", "value": [EVIL, "ok"]},
        {"key": EVIL, "operator": "contains", "value": EVIL},
        {"key": EVIL, "operator": "gt", "value": 3},
        {"key": EVIL, "operator": "is_set"},
    ]


def all_compiled(project_id: int = 42) -> list[CompiledQuery]:
    rng = {"date_from": "2026-06-01", "date_to": "2026-06-30"}
    trend = TrendQuery.model_validate(
        {
            "series": [
                {"event": EVIL, "math": "unique_persons", "filters": evil_filters()},
                {"event": EVIL, "math": "sum", "math_property": EVIL},
            ],
            "breakdown": EVIL,
            "date_range": rng,
            "filters": evil_filters(),
        }
    )
    funnel = FunnelQuery.model_validate(
        {
            "steps": [{"event": EVIL, "filters": evil_filters()}, {"event": EVIL}],
            "breakdown": EVIL,
            "date_range": rng,
            "filters": evil_filters(),
        }
    )
    retention = RetentionQuery.model_validate(
        {
            "start_event": {"event": EVIL, "filters": evil_filters()},
            "return_event": {"event": EVIL},
            "date_range": rng,
            "filters": evil_filters(),
        }
    )
    return [
        compile_trend_series(trend, 0, project_id=project_id, now=NOW),
        compile_trend_series(trend, 1, project_id=project_id, now=NOW),
        compile_funnel(funnel, project_id=project_id, now=NOW),
        compile_retention(retention, project_id=project_id, now=NOW),
        compile_property_summary(
            project_id=project_id, event=EVIL, start=NOW, end=NOW + timedelta(days=1)
        ),
        compile_event_summary(project_id=project_id, start=NOW, end=NOW + timedelta(days=1)),
    ]


def test_user_strings_never_reach_the_sql_text() -> None:
    for compiled in all_compiled()[:-1]:  # the event summary takes no user strings
        assert "OR 1=1" not in compiled.sql
        assert "system.users" not in compiled.sql
        assert (
            EVIL in compiled.params.values()
            or [EVIL] in compiled.params.values()
            or any(isinstance(v, list) and EVIL in v for v in compiled.params.values())
        )


def test_every_placeholder_has_a_param_and_types_are_constants() -> None:
    for compiled in all_compiled():
        placeholders = re.findall(r"\{(\w+):([^{}]+)\}", compiled.sql)
        assert placeholders
        for name, _type in placeholders:
            assert name in compiled.params


def test_every_table_read_is_scoped_to_the_project() -> None:
    project = "{project_id:UInt64}"
    for compiled in all_compiled():
        event_reads = re.findall(r"FROM events\b", compiled.sql)
        assert event_reads
        assert compiled.sql.count("FROM events AS e FINAL") == len(event_reads)
        assert compiled.sql.count(f"WHERE e.project_id = {project}") == len(event_reads)
        person_reads = re.findall(r"FROM person_distinct_id\b", compiled.sql)
        assert compiled.sql.count(f"FROM person_distinct_id WHERE project_id = {project}") == len(
            person_reads
        )
        assert compiled.params["project_id"] == 42


def test_no_other_table_is_ever_named() -> None:
    for compiled in all_compiled():
        tables = set(re.findall(r"\bFROM\s+([A-Za-z_][\w.]*)", compiled.sql))
        assert tables <= {
            "events",
            "person_distinct_id",
            "base",
            "starts",
            "returns",
            "top",
            "sampled",
        }


@pytest.mark.parametrize("bad", [0, -1, True, "42"])
def test_project_id_must_be_a_real_id(bad: object) -> None:
    query = TrendQuery.model_validate({"series": [{"event": "a"}]})
    with pytest.raises(ValueError, match="project_id"):
        compile_trend_series(query, 0, project_id=bad, now=NOW)  # type: ignore[arg-type]


def test_query_cannot_carry_a_project_id_or_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="project_id"):
        ADAPTER.validate_python({"kind": "trend", "series": [{"event": "a"}], "project_id": 7})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ADAPTER.validate_python({"kind": "trend", "series": [{"event": "a", "fliters": []}]})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"key": "plan", "operator": "gt", "value": "pro"}, "needs a number"),
        ({"key": "plan", "operator": "is_set", "value": "x"}, "takes no value"),
        ({"key": "plan", "operator": "eq"}, "needs a value"),
        ({"key": "plan", "operator": "eq", "value": []}, "empty"),
    ],
)
def test_filters_are_validated_with_actionable_messages(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ADAPTER.validate_python({"kind": "trend", "series": [{"event": "a", "filters": [payload]}]})


def test_property_math_requires_a_property() -> None:
    with pytest.raises(ValidationError, match="math_property"):
        ADAPTER.validate_python({"kind": "trend", "series": [{"event": "a", "math": "sum"}]})
