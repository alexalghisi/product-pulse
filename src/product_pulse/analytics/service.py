"""Project-scoped analytics operations shared by the REST API and the MCP server.

A service instance is bound to one project when it is created, from the caller's
credentials. Nothing it exposes takes a project id, so neither an HTTP client nor an
agent can point it at another project.

Errors are written for whoever has to fix the call: an unknown event name comes back
with the closest names that do exist, which is usually all an agent needs to retry.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from product_pulse.clickhouse.client import QueryExecutor
from product_pulse.core.models import Insight
from product_pulse.query.compiler import compile_event_summary, compile_property_summary
from product_pulse.query.models import (
    FunnelQuery,
    InsightQuery,
    PropertyFilter,
    RetentionQuery,
    TrendQuery,
)
from product_pulse.query.runner import QueryResult, QueryRunner

DISCOVERY_WINDOW = timedelta(days=365)
QUERY_ADAPTER: TypeAdapter[InsightQuery] = TypeAdapter(InsightQuery)


@dataclass
class AnalyticsError(Exception):
    code: str
    message: str
    suggestions: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.suggestions:
            return f"{self.message} Did you mean: {', '.join(self.suggestions)}?"
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "suggestions": self.suggestions}


class EventSummary(BaseModel):
    name: str
    count: int
    last_seen: datetime


class PropertySummary(BaseModel):
    key: str
    type: str
    seen_in: float  # share of sampled events that have it
    examples: list[str]


class InsightOut(BaseModel):
    id: int
    name: str
    description: str
    query: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, insight: Insight) -> InsightOut:
        return cls(
            id=insight.pk,
            name=insight.name,
            description=insight.description,
            query=insight.query,
            created_at=insight.created_at,
            updated_at=insight.updated_at,
        )


def closest(name: str, candidates: Iterable[str], n: int = 3) -> list[str]:
    pool = list(candidates)
    lowered = {c.lower(): c for c in pool}
    matches = difflib.get_close_matches(name.lower(), list(lowered), n=n, cutoff=0.5)
    # difflib misses "signup" -> "user_signed_up"; a substring check catches those.
    for candidate in pool:
        if len(matches) >= n:
            break
        bare = name.lower().strip("$_ ")
        if bare and bare in candidate.lower() and candidate.lower() not in matches:
            matches.append(candidate.lower())
    return [lowered[m] for m in matches]


def parse_query(data: object) -> InsightQuery:
    try:
        return QUERY_ADAPTER.validate_python(data)
    except ValidationError as exc:
        problems = []
        for error in exc.errors()[:5]:
            location = ".".join(str(p) for p in error["loc"])
            problems.append(f"{location}: {error['msg']}" if location else error["msg"])
        raise AnalyticsError("invalid_query", "Invalid query. " + "; ".join(problems)) from exc


def _filters(query: InsightQuery) -> Iterable[tuple[str | None, PropertyFilter]]:
    """(event the filter applies to, or None for all events of the query, filter)."""
    for f in query.filters:
        yield None, f
    if isinstance(query, TrendQuery):
        for s in query.series:
            yield from ((s.event, f) for f in s.filters)
    elif isinstance(query, FunnelQuery):
        for step in query.steps:
            yield from ((step.event, f) for f in step.filters)
    else:
        yield from ((query.start_event.event, f) for f in query.start_event.filters)
        yield from ((query.return_event.event, f) for f in query.return_event.filters)


def events_of(query: InsightQuery) -> list[str]:
    if isinstance(query, TrendQuery):
        return [s.event for s in query.series]
    if isinstance(query, FunnelQuery):
        return [s.event for s in query.steps]
    assert isinstance(query, RetentionQuery)
    return [query.start_event.event, query.return_event.event]


class AnalyticsService:
    def __init__(
        self,
        project_id: int,
        executor: QueryExecutor,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.project_id = project_id
        self.executor = executor
        self.clock = clock
        self._events: list[EventSummary] | None = None
        self._properties: dict[str, list[PropertySummary]] = {}

    # -- discovery -----------------------------------------------------------------------

    def list_events(self) -> list[EventSummary]:
        if self._events is None:
            now = self.clock()
            compiled = compile_event_summary(
                project_id=self.project_id,
                start=now - DISCOVERY_WINDOW,
                end=now + timedelta(hours=1),
            )
            self._events = [
                EventSummary(
                    name=row["event"],
                    count=int(row["count"]),
                    last_seen=datetime.fromisoformat(row["last_seen"]).replace(tzinfo=UTC),
                )
                for row in self.executor.query(compiled.sql, compiled.params)
            ]
        return self._events

    def require_event(self, name: str) -> None:
        known = [e.name for e in self.list_events()]
        if name not in known:
            raise AnalyticsError(
                "unknown_event",
                f"No event named {name!r} in this project (names are case-sensitive).",
                closest(name, known),
            )

    def event_properties(self, event: str) -> list[PropertySummary]:
        self.require_event(event)
        if event not in self._properties:
            now = self.clock()
            compiled = compile_property_summary(
                project_id=self.project_id,
                event=event,
                start=now - DISCOVERY_WINDOW,
                end=now + timedelta(hours=1),
            )
            rows = self.executor.query(compiled.sql, compiled.params)
            self._properties[event] = [
                PropertySummary(
                    key=row["key"],
                    type=str(row["type"]).lower(),
                    seen_in=round(int(row["events"]) / max(int(row["sampled_events"]), 1), 3),
                    examples=[str(v) for v in row["examples"] if v != ""],
                )
                for row in rows
            ]
        return self._properties[event]

    def _require_property(self, key: str, events: Sequence[str], role: str) -> None:
        known: set[str] = set()
        for event in events:
            known.update(p.key for p in self.event_properties(event))
        if key not in known:
            raise AnalyticsError(
                "unknown_property",
                f"{role} property {key!r} is never set on {', '.join(map(repr, events))}.",
                closest(key, known),
            )

    # -- queries -------------------------------------------------------------------------

    def validate(self, query: InsightQuery) -> None:
        """Check names against what the project actually has, before spending a query."""
        events = events_of(query)
        for event in dict.fromkeys(events):
            self.require_event(event)
        for scope, f in _filters(query):
            self._require_property(f.key, [scope] if scope else events, "Filter")
        breakdown = getattr(query, "breakdown", None)
        if breakdown:
            source = events[:1] if isinstance(query, FunnelQuery) else events
            self._require_property(breakdown, source, "Breakdown")
        if isinstance(query, TrendQuery):
            for s in query.series:
                if s.math_property:
                    self._require_property(s.math_property, [s.event], "math_property")

    def run(self, query: InsightQuery) -> QueryResult:
        self.validate(query)
        try:
            return QueryRunner(self.executor).run(
                query, project_id=self.project_id, now=self.clock()
            )
        except ValueError as exc:  # e.g. too many buckets, empty date range
            raise AnalyticsError("invalid_query", str(exc)) from exc

    # -- saved insights ------------------------------------------------------------------

    def list_insights(self) -> list[InsightOut]:
        return [
            InsightOut.from_model(i) for i in Insight.objects.filter(project_id=self.project_id)
        ]

    def get_insight(self, insight_id: int) -> Insight:
        insight = Insight.objects.filter(project_id=self.project_id, pk=insight_id).first()
        if insight is None:
            raise AnalyticsError("not_found", f"No insight {insight_id} in this project.")
        return insight

    def create_insight(
        self, *, name: str, query: InsightQuery, description: str = "", user: Any = None
    ) -> InsightOut:
        if not name.strip():
            raise AnalyticsError("invalid_insight", "Insight name must not be blank.")
        self.validate(query)
        insight = Insight.objects.create(
            project_id=self.project_id,
            name=name.strip()[:200],
            description=description,
            query=query.model_dump(mode="json"),
            created_by=user,
        )
        return InsightOut.from_model(insight)

    def insight_result(self, insight_id: int) -> QueryResult:
        return self.run(parse_query(self.get_insight(insight_id).query))
