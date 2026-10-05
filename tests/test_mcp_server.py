"""The MCP server, driven through a real MCP client session."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.core.models import Insight, Project
from product_pulse.mcp_server.server import build_server

from .dataset import load, noise_events, reference_events

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.clickhouse]

JUNE = {"date_from": "2026-06-01", "date_to": "2026-06-30"}


@pytest.fixture
def connect(
    clickhouse: ClickHouseClient, project: Project, other_project: Project
) -> Callable[[Callable[[Client], Awaitable[None]]], None]:
    load(clickhouse, reference_events(project.pk))
    load(clickhouse, noise_events(other_project.pk))
    server = build_server(lambda: project.pk, lambda: clickhouse)

    def run(body: Callable[[Client], Awaitable[None]]) -> None:
        async def main() -> None:
            async with Client(server) as client:
                await body(client)

        anyio.run(main)

    return run


def text(result: CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def data(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, text(result)
    if result.structured_content is not None:
        return dict(result.structured_content)
    parsed: dict[str, Any] = json.loads(text(result))
    return parsed


def test_tools_are_described_for_a_model(connect: Any) -> None:
    async def body(client: Client) -> None:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {
            "list_events",
            "get_event_properties",
            "run_trend",
            "run_funnel",
            "run_retention",
            "create_insight",
            "list_insights",
        }
        for tool in tools.values():
            assert tool.description and len(tool.description) > 60
        assert tools["run_trend"].annotations and tools["run_trend"].annotations.read_only_hint
        assert not (
            tools["create_insight"].annotations
            and tools["create_insight"].annotations.read_only_hint
        )
        series = tools["run_trend"].input_schema["properties"]["series"]
        assert "unique_persons" in json.dumps(series) or "unique_persons" in json.dumps(
            tools["run_trend"].input_schema
        )

    connect(body)


def test_discovery(connect: Any) -> None:
    async def body(client: Client) -> None:
        events = data(await client.call_tool("list_events", {}))
        assert events["events"][0] == {
            "name": "$pageview",
            "count": 7,
            "last_seen": "2026-06-03T09:00",
        }
        props = data(await client.call_tool("get_event_properties", {"event": "signed_up"}))
        assert props["properties"][0]["key"] == "plan"
        assert set(props["properties"][0]["examples"]) == {"free", "pro"}

    connect(body)


def test_unknown_names_come_back_with_suggestions(connect: Any) -> None:
    async def body(client: Client) -> None:
        result = await client.call_tool("run_trend", {"series": [{"event": "signup"}]})
        assert result.is_error
        assert "signed_up" in text(result)
        result = await client.call_tool(
            "run_funnel", {"steps": ["signed_up", "project_created"], "breakdown": "Plan"}
        )
        assert result.is_error and "plan" in text(result)
        result = await client.call_tool(
            "run_trend", {"series": [{"event": "$pageview"}], "date_from": "last week"}
        )
        assert result.is_error and "YYYY-MM-DD" in text(result)

    connect(body)


def test_queries_answer_correctly(connect: Any) -> None:
    async def body(client: Client) -> None:
        trend = data(
            await client.call_tool(
                "run_trend", {"series": [{"event": "$pageview", "math": "unique_persons"}], **JUNE}
            )
        )
        assert trend["series"][0]["total"] == 3
        assert len(trend["buckets"]) == 30

        funnel = data(
            await client.call_tool(
                "run_funnel",
                {
                    "steps": ["signed_up", "project_created", "invited_teammate"],
                    "conversion_window": 1,
                    "breakdown": "plan",
                    **JUNE,
                },
            )
        )
        assert [s["persons"] for s in funnel["overall"]] == [3, 2, 1]
        assert {b["breakdown_value"] for b in funnel["breakdowns"]} == {"free", "pro"}

        filtered = data(
            await client.call_tool(
                "run_funnel",
                {
                    "steps": ["signed_up", "project_created"],
                    "conversion_window": 1,
                    "step_filters": {"0": [{"key": "plan", "value": "free"}]},
                    **JUNE,
                },
            )
        )
        assert [s["persons"] for s in filtered["overall"]] == [2, 1]

        retention = data(
            await client.call_tool(
                "run_retention",
                {
                    "start_event": "app_opened",
                    "return_event": "app_opened",
                    "period": "day",
                    "periods": 3,
                    "date_from": "2026-06-01",
                    "date_to": "2026-06-02",
                },
            )
        )
        assert retention["cohorts"][0]["retained"] == [1, 2, 0]

    connect(body)


def test_create_insight_validates_and_saves(connect: Any, project: Project) -> None:
    async def body(client: Client) -> None:
        bad = await client.call_tool(
            "create_insight", {"name": "x", "query": {"kind": "funnel", "steps": []}}
        )
        assert bad.is_error and "steps" in text(bad)
        saved = data(
            await client.call_tool(
                "create_insight",
                {
                    "name": "Signup to project",
                    "query": {
                        "kind": "funnel",
                        "steps": [{"event": "signed_up"}, {"event": "project_created"}],
                    },
                },
            )
        )
        assert saved["query"]["conversion_window"] == 14
        listed = data(await client.call_tool("list_insights", {}))
        assert [i["name"] for i in listed["insights"]] == ["Signup to project"]

    connect(body)
    assert Insight.objects.filter(project=project).count() == 1
