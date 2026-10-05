"""The eval suite itself: expected answers hold, the offline baseline scores as documented,
and the OpenAI-compatible client drives the tools correctly."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import anyio
import httpx
import pytest
from mcp import Client

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.core.models import Project
from product_pulse.evals.harness import ToolBox, extract, load_questions, parse_number
from product_pulse.evals.offline import OfflinePlanner, mentioned_events
from product_pulse.evals.openai_client import OpenAICompatibleClient, OpenAIConfig
from product_pulse.evals.runner import load_fixture, run
from product_pulse.mcp_server.server import build_server

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.clickhouse]

# The offline planner is a keyword baseline. These are the questions it is known to get
# wrong, and why: see the docstring of product_pulse.evals.offline.
OFFLINE_MISSES = {"paraphrase_share_rate", "two_queries_growth", "best_source_signups"}


@pytest.fixture
def eval_project(clickhouse: ClickHouseClient, project: Project) -> Iterator[int]:
    load_fixture(project.pk, clickhouse)
    yield project.pk


def test_expected_answers_match_the_engine(eval_project: int, clickhouse: ClickHouseClient) -> None:
    """Answers were derived by hand from the fixture's rules; the engine must agree."""
    server = build_server(lambda: eval_project, lambda: clickhouse)

    async def main() -> dict[str, float]:
        got: dict[str, float] = {}
        async with Client(server) as client:
            for q in load_questions():
                total = 0.0
                for ref in q.reference:
                    result = await client.call_tool(ref.tool, ref.args)
                    assert not result.is_error, (q.id, result.content)
                    total += ref.sign * ref.scale * extract(result.structured_content, ref.path)
                got[q.id] = round(total, 2)
        return got

    got = anyio.run(main)
    expected = {q.id: q.answer for q in load_questions()}
    assert got == expected


def test_offline_planner_scores_as_documented(
    eval_project: int, clickhouse: ClickHouseClient
) -> None:
    report = anyio.run(run, eval_project, clickhouse, OfflinePlanner(), load_questions())
    misses = {o.question.id for o in report.outcomes if not o.correct}
    assert misses == OFFLINE_MISSES
    assert report.tool_selection == 1.0


def chat(message: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": message}]})


def tool_call(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_openai_compatible_client_runs_the_tool_loop(
    eval_project: int, clickhouse: ClickHouseClient
) -> None:
    requests: list[dict[str, Any]] = []
    script = [
        chat(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tool_call("c1", "list_events", "{}")],
            }
        ),
        chat(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [tool_call("c2", "run_trend", "{not json")],
            }
        ),
        chat(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    tool_call(
                        "c3",
                        "run_trend",
                        json.dumps(
                            {
                                "series": [{"event": "signup", "math": "unique_persons"}],
                                "date_from": "2026-06-01",
                                "date_to": "2026-06-30",
                            }
                        ),
                    )
                ],
            }
        ),
        chat(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    tool_call(
                        "c4",
                        "run_trend",
                        json.dumps(
                            {
                                "series": [{"event": "signed_up", "math": "unique_persons"}],
                                "date_from": "2026-06-01",
                                "date_to": "2026-06-30",
                            }
                        ),
                    )
                ],
            }
        ),
        chat({"role": "assistant", "content": '{"answer": 200}'}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-key"
        return script[len(requests) - 1]

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleClient(
        OpenAIConfig(api_key="test-key", base_url="http://llm.test/v1"), http
    )
    server = build_server(lambda: eval_project, lambda: clickhouse)

    async def main() -> tuple[float | None, ToolBox]:
        async with Client(server) as client:
            tools = ToolBox(client)
            await tools.load()
            answer = await model.answer("How many people signed up in June 2026?", tools)
            return answer.value, tools

    value, tools = anyio.run(main)
    assert value == 200
    assert [(c.name, c.ok) for c in tools.calls] == [
        ("list_events", True),
        ("run_trend", False),
        ("run_trend", True),
    ]
    sent_tools = {t["function"]["name"] for t in requests[0]["tools"]}
    assert "run_funnel" in sent_tools
    # The model saw the bad-JSON complaint and the unknown-event suggestion.
    tool_messages = [m["content"] for m in requests[3]["messages"] if m["role"] == "tool"]
    assert "not valid JSON" in tool_messages[1]
    assert "signed_up" in tool_messages[2]


@pytest.mark.parametrize(
    ("text", "value"),
    [('{"answer": 41.5}', 41.5), ("The answer is 1,234 users.", 1234), ("no idea", None)],
)
def test_parse_number(text: str, value: float | None) -> None:
    assert parse_number(text) == value


def test_event_mentions_follow_question_order() -> None:
    names = ["$pageview", "signed_up", "note_created", "note_shared", "app_opened"]
    assert mentioned_events("Of people who signed up, how many created a note?", names) == [
        "signed_up",
        "note_created",
    ]
    assert mentioned_events("How many users viewed a page?", names) == ["$pageview"]
