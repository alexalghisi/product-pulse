"""MCP server: the analytics service as tools a model can call (started by `cli.py`).

A key is bound to one project, so no tool takes a project id: the scope comes from the
credentials, and a model can't wander into another project by guessing ids.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

import anyio.to_thread
from django.db import connections
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field, ValidationError

from product_pulse.analytics.service import AnalyticsError, AnalyticsService, parse_query
from product_pulse.clickhouse.client import ClickHouseError, QueryExecutor
from product_pulse.core.models import PersonalAPIKey
from product_pulse.mcp_server import formatting
from product_pulse.query.models import (
    DateRange,
    FunnelQuery,
    FunnelStep,
    PropertyFilter,
    RetentionEvent,
    RetentionQuery,
    TrendQuery,
    TrendSeries,
)

INSTRUCTIONS = """\
Product analytics for one project: events captured from an app, grouped into persons.

How to work with it:
1. Call list_events first. Event names are exact and case-sensitive ('$pageview', 'signed_up').
2. Call get_event_properties before filtering or breaking down, to see real keys and values.
3. Pick the query tool by the question:
   - counts or sums over time -> run_trend
     ("how many users" = math 'unique_persons'; "how many times" = math 'total')
   - conversion through ordered steps -> run_funnel
   - whether people come back after a first action -> run_retention
4. Dates are UTC. Use 'YYYY-MM-DD' (date_to is inclusive) or relative values like '-30d'.
5. Errors explain what to change and often suggest the right name: retry with it.
Answer from the returned numbers; 'total' in a trend is the value over the whole range.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

ProjectResolver = Callable[[], int]
ExecutorFactory = Callable[[], QueryExecutor]

Filters = Annotated[
    list[PropertyFilter] | None,
    Field(
        description=(
            "Property filters, all must match. Example: [{'key': '$browser', 'value': 'Chrome'}]"
        )
    ),
]
DateFrom = Annotated[str, Field(description="'YYYY-MM-DD' or relative like '-30d', '-12w', '-3m'.")]
DateTo = Annotated[str | None, Field(description="'YYYY-MM-DD', inclusive. Omit to run up to now.")]


def _tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, AnalyticsError):
        return ToolError(str(exc))
    if isinstance(exc, ValidationError):
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'query'}: {e['msg']}" for e in exc.errors()[:5]
        )
        return ToolError(f"Invalid arguments: {problems}")
    if isinstance(exc, ClickHouseError):
        return ToolError("The analytics database could not run this query. Try again shortly.")
    raise exc


def build_server(
    resolve_project: ProjectResolver,
    executor_factory: ExecutorFactory,
    *,
    auth: tuple[AuthSettings, Any] | None = None,
) -> MCPServer:
    """Create the server. `resolve_project` is called per tool call, inside the request."""
    options: dict[str, Any] = {}
    if auth is not None:
        options["auth"], options["token_verifier"] = auth
    server: MCPServer = MCPServer(
        "product-pulse",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        log_level="WARNING",
        **options,
    )

    async def call[T](work: Callable[[AnalyticsService], T]) -> T:
        project_id = resolve_project()

        def run() -> T:
            try:
                return work(AnalyticsService(project_id, executor_factory()))
            finally:
                # Tool calls run on a thread pool; don't leave a Postgres connection per thread.
                connections.close_all()

        try:
            return await anyio.to_thread.run_sync(run)
        except (AnalyticsError, ValidationError, ClickHouseError) as exc:
            raise _tool_error(exc) from exc

    @server.tool(annotations=READ_ONLY)
    async def list_events() -> dict[str, Any]:
        """List the event names captured in this project, most frequent first, with counts
        and when each was last seen (over the past year). Call this before any query: names
        must match exactly."""
        return await call(lambda s: formatting.events(s.list_events()))

    @server.tool(annotations=READ_ONLY)
    async def get_event_properties(
        event: Annotated[str, Field(description="Exact event name from list_events.")],
    ) -> dict[str, Any]:
        """Describe the properties set on an event: key, JSON type, the share of events that
        have it (seen_in, 0-1) and up to five common values. Use it to choose filters and
        breakdowns; values are matched exactly (as text) by the 'eq' operator."""
        return await call(lambda s: formatting.properties(event, s.event_properties(event)))

    @server.tool(annotations=READ_ONLY)
    async def run_trend(
        series: Annotated[
            list[TrendSeries],
            Field(
                min_length=1,
                max_length=5,
                description=(
                    "What to count. Example: [{'event': 'signed_up', 'math': 'unique_persons'}]."
                    " math: total (events), unique_persons (people), sum/avg/min/max of"
                    " math_property."
                ),
            ),
        ],
        date_from: DateFrom = "-30d",
        date_to: DateTo = None,
        interval: Literal["hour", "day", "week", "month"] = "day",
        breakdown: Annotated[
            str | None, Field(description="Property key to split each series by.")
        ] = None,
        breakdown_limit: Annotated[int, Field(ge=1, le=50)] = 10,
        filters: Filters = None,
    ) -> dict[str, Any]:
        """Count or aggregate events over time. Returns, per series (and per breakdown value),
        the value in each time bucket and a 'total' over the whole range. For questions like
        'how many people did X in June', read 'total' with math 'unique_persons'."""

        def work(s: AnalyticsService) -> dict[str, Any]:
            query = TrendQuery(
                series=series,
                date_range=DateRange(date_from=date_from, date_to=date_to),
                interval=interval,
                breakdown=breakdown,
                breakdown_limit=breakdown_limit,
                filters=filters or [],
            )
            result = s.run(query)
            assert result.kind == "trend"
            return formatting.trend(result)

        return await call(work)

    @server.tool(annotations=READ_ONLY)
    async def run_funnel(
        steps: Annotated[
            list[str],
            Field(
                min_length=2,
                max_length=10,
                description="Event names in order, e.g. ['signed_up', 'project_created'].",
            ),
        ],
        conversion_window: Annotated[int, Field(ge=1, le=365)] = 14,
        conversion_window_unit: Literal["minute", "hour", "day", "week"] = "day",
        date_from: DateFrom = "-30d",
        date_to: DateTo = None,
        breakdown: Annotated[
            str | None,
            Field(
                description=(
                    "Property key; each person is counted under the value on their first step."
                )
            ),
        ] = None,
        step_filters: Annotated[
            dict[int, list[PropertyFilter]] | None,
            Field(description="Filters for individual steps by 0-based index, e.g. {0: [...]}."),
        ] = None,
        filters: Filters = None,
    ) -> dict[str, Any]:
        """Measure how many persons complete ordered steps. Step 1 must happen within the date
        range; each later step must follow within the conversion window counted from step 1.
        Returns persons per step, conversion_from_start and conversion_from_previous (0-1)."""

        def work(s: AnalyticsService) -> dict[str, Any]:
            extra = step_filters or {}
            unknown = [i for i in extra if not 0 <= i < len(steps)]
            if unknown:
                raise AnalyticsError(
                    "invalid_query", f"step_filters index {unknown} is outside 0..{len(steps) - 1}."
                )
            query = FunnelQuery(
                steps=[FunnelStep(event=e, filters=extra.get(i, [])) for i, e in enumerate(steps)],
                conversion_window=conversion_window,
                conversion_window_unit=conversion_window_unit,
                date_range=DateRange(date_from=date_from, date_to=date_to),
                breakdown=breakdown,
                filters=filters or [],
            )
            result = s.run(query)
            assert result.kind == "funnel"
            return formatting.funnel(result)

        return await call(work)

    @server.tool(annotations=READ_ONLY)
    async def run_retention(
        start_event: Annotated[str, Field(description="Event that puts a person in a cohort.")],
        return_event: Annotated[str, Field(description="Event that counts as coming back.")],
        period: Literal["day", "week"] = "week",
        periods: Annotated[int, Field(ge=1, le=30)] = 8,
        date_from: DateFrom = "-8w",
        date_to: DateTo = None,
        filters: Filters = None,
    ) -> dict[str, Any]:
        """Cohort retention. Persons are grouped by the period they first did start_event in the
        range; for each later period k (1..periods) it returns how many did return_event in that
        period, and the rate. Week periods start on Monday."""

        def work(s: AnalyticsService) -> dict[str, Any]:
            query = RetentionQuery(
                start_event=RetentionEvent(event=start_event, filters=[]),
                return_event=RetentionEvent(event=return_event, filters=[]),
                period=period,
                periods=periods,
                date_range=DateRange(date_from=date_from, date_to=date_to),
                filters=filters or [],
            )
            result = s.run(query)
            assert result.kind == "retention"
            return formatting.retention(result)

        return await call(work)

    @server.tool()
    async def create_insight(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        query: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "A full query with 'kind' = 'trend' | 'funnel' | 'retention', in the same shape"
                    " the run_* tools build. Example: {'kind': 'funnel', 'steps': [{'event':"
                    " 'signed_up'}, {'event': 'project_created'}], 'conversion_window': 7}."
                    " Use 'date_range': {'date_from': ..., 'date_to': ...} for dates."
                )
            ),
        ],
        description: str = "",
    ) -> dict[str, Any]:
        """Save a query as an insight so people see it in the web app. The query is validated
        against the project's events first. Returns the saved insight with its id."""

        def work(s: AnalyticsService) -> dict[str, Any]:
            saved = s.create_insight(name=name, description=description, query=parse_query(query))
            return saved.model_dump(mode="json")

        return await call(work)

    @server.tool(annotations=READ_ONLY)
    async def list_insights() -> dict[str, Any]:
        """List the insights people (or agents) have saved in this project: id, name,
        description and the stored query, most recently updated first. Up to 50."""
        return await call(
            lambda s: {"insights": [i.model_dump(mode="json") for i in s.list_insights()[:50]]}
        )

    return server


# -- wiring ---------------------------------------------------------------------------------


class PersonalKeyVerifier:
    """Accepts personal API keys as bearer tokens for the HTTP transport."""

    async def verify_token(self, token: str) -> AccessToken | None:
        key = await anyio.to_thread.run_sync(PersonalAPIKey.verify, token)
        if key is None:
            return None
        return AccessToken(
            token=token,
            client_id=key.prefix,
            scopes=["analytics"],
            subject=str(key.user_id),
            claims={"project_id": key.project_id},
        )


def project_from_access_token() -> int:
    token = get_access_token()
    if token is None or not token.claims or "project_id" not in token.claims:
        raise ToolError("Not authenticated: send a personal API key as a bearer token.")
    return int(token.claims["project_id"])


def project_from_key(plaintext: str) -> int:
    key = PersonalAPIKey.verify(plaintext)
    if key is None:
        raise SystemExit("PULSE_API_KEY is not a valid personal API key")
    return key.project_id
