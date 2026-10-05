"""The typed query model: what a person (the UI) or an agent (the API, MCP) can ask.

There is no free-form SQL. A query is one of three shapes, each validated here and
compiled by `compiler.py`. Field descriptions end up in the JSON schema that the API
publishes and the MCP tools expose, so they are written for whoever builds the query,
including a model that has never seen this product.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EventName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        description="Exact event name as captured, e.g. '$pageview' or 'signed_up'.",
    ),
]
PropertyKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        description="Event property name, e.g. '$browser' or 'plan'. Top-level keys only.",
    ),
]

Operator = Literal[
    "eq", "neq", "contains", "not_contains", "gt", "gte", "lt", "lte", "is_set", "is_not_set"
]
NUMERIC_OPERATORS = frozenset({"gt", "gte", "lt", "lte"})
PRESENCE_OPERATORS = frozenset({"is_set", "is_not_set"})

_RELATIVE = re.compile(r"^-(\d{1,4})([hdwmy])$")
_ABSOLUTE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?$")


class _Strict(BaseModel):
    # Unknown fields are an error, not ignored: a typo in a filter must not silently
    # widen a query, and `project_id` can never be smuggled in through the body.
    model_config = ConfigDict(extra="forbid", frozen=True)


class PropertyFilter(_Strict):
    key: PropertyKey
    operator: Operator = Field(
        default="eq",
        description=(
            "eq/neq compare as text (value may be a list for 'any of'); contains/not_contains "
            "are case-insensitive substring matches; gt/gte/lt/lte compare numerically; "
            "is_set/is_not_set take no value."
        ),
    )
    value: str | float | list[str] | None = Field(default=None, description="Value to compare.")

    @model_validator(mode="after")
    def _value_matches_operator(self) -> PropertyFilter:
        if self.operator in PRESENCE_OPERATORS:
            if self.value is not None:
                raise ValueError(f"operator '{self.operator}' takes no value")
        elif self.value is None:
            raise ValueError(f"operator '{self.operator}' needs a value")
        elif self.operator in NUMERIC_OPERATORS and not isinstance(self.value, int | float):
            raise ValueError(f"operator '{self.operator}' needs a number")
        elif self.operator in {"contains", "not_contains"} and not isinstance(self.value, str):
            raise ValueError(f"operator '{self.operator}' needs a string")
        elif isinstance(self.value, list) and not self.value:
            raise ValueError("value list is empty")
        return self


Filters = Annotated[
    list[PropertyFilter],
    Field(default_factory=list, max_length=20, description="All filters must match (AND)."),
]


def _check_date(value: str) -> str:
    value = value.strip()
    if _RELATIVE.match(value) or _ABSOLUTE.match(value):
        return value
    raise ValueError(
        f"unrecognised date {value!r}: use 'YYYY-MM-DD' or a relative value like '-7d', "
        "'-24h', '-4w', '-3m', '-1y'"
    )


class DateRange(_Strict):
    date_from: str = Field(
        default="-30d",
        description="Start: 'YYYY-MM-DD' (start of that day, UTC) or relative like '-7d', '-24h'.",
    )
    date_to: str | None = Field(
        default=None,
        description="End: 'YYYY-MM-DD' is inclusive (through the end of that day). Omit for now.",
    )

    @field_validator("date_from")
    @classmethod
    def _from(cls, value: str) -> str:
        return _check_date(value)

    @field_validator("date_to")
    @classmethod
    def _to(cls, value: str | None) -> str | None:
        return None if value is None else _check_date(value)


Math = Literal["total", "unique_persons", "sum", "avg", "min", "max"]


class TrendSeries(_Strict):
    event: EventName
    math: Math = Field(
        default="total",
        description=(
            "total = number of events; unique_persons = distinct people (anonymous and "
            "identified ids of one person count once); sum/avg/min/max aggregate the numeric "
            "property named in math_property."
        ),
    )
    math_property: PropertyKey | None = None
    filters: Filters

    @model_validator(mode="after")
    def _property_math(self) -> TrendSeries:
        needs_property = self.math in {"sum", "avg", "min", "max"}
        if needs_property and not self.math_property:
            raise ValueError(f"math '{self.math}' needs math_property")
        if not needs_property and self.math_property:
            raise ValueError(f"math '{self.math}' does not use math_property")
        return self


class TrendQuery(_Strict):
    kind: Literal["trend"] = "trend"
    series: list[TrendSeries] = Field(min_length=1, max_length=5)
    date_range: DateRange = Field(default_factory=DateRange)
    interval: Literal["hour", "day", "week", "month"] = Field(
        default="day", description="Bucket size. Weeks start on Monday. All times are UTC."
    )
    breakdown: PropertyKey | None = Field(
        default=None, description="Split each series by this event property."
    )
    breakdown_limit: int = Field(
        default=10, ge=1, le=50, description="Top values kept; the rest are grouped as '$other'."
    )
    filters: Filters


class FunnelStep(_Strict):
    event: EventName
    filters: Filters


class FunnelQuery(_Strict):
    kind: Literal["funnel"] = "funnel"
    steps: list[FunnelStep] = Field(min_length=2, max_length=10)
    conversion_window: int = Field(
        default=14, ge=1, le=365, description="How long a person has, from step 1, to finish."
    )
    conversion_window_unit: Literal["minute", "hour", "day", "week"] = "day"
    date_range: DateRange = Field(
        default_factory=DateRange, description="Window in which step 1 must happen."
    )
    breakdown: PropertyKey | None = Field(
        default=None,
        description="Split by this property, taken from the person's first step-1 event.",
    )
    breakdown_limit: int = Field(default=10, ge=1, le=50)
    filters: Filters

    def window_seconds(self) -> int:
        unit = {"minute": 60, "hour": 3600, "day": 86_400, "week": 604_800}
        return self.conversion_window * unit[self.conversion_window_unit]


class RetentionEvent(_Strict):
    event: EventName
    filters: Filters


class RetentionQuery(_Strict):
    kind: Literal["retention"] = "retention"
    start_event: RetentionEvent = Field(
        description="Puts a person in the cohort of the period they first did it in the range."
    )
    return_event: RetentionEvent = Field(description="Counts as coming back.")
    period: Literal["day", "week"] = "week"
    periods: int = Field(default=8, ge=1, le=30, description="Periods after the cohort to show.")
    date_range: DateRange = Field(
        default_factory=lambda: DateRange(date_from="-8w"),
        description="Range in which cohorts start.",
    )
    filters: Filters


InsightQuery = Annotated[TrendQuery | FunnelQuery | RetentionQuery, Field(discriminator="kind")]
