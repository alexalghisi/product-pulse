"""Eval harness: questions, the tool box a model drives, scoring and the report.

Every tool call goes through a real MCP client session, so the evals exercise the same
schemas, descriptions, truncation and error messages a production agent would see.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Protocol

from mcp import Client
from mcp_types import TextContent


@dataclass(frozen=True)
class ReferenceCall:
    tool: str
    args: dict[str, Any]
    path: str
    scale: float = 1.0
    sign: int = 1


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    expected_tool: str
    answer: float
    reference: list[ReferenceCall]
    tolerance: float = 0.5


def load_questions() -> list[Question]:
    raw = json.loads(resources.files("product_pulse.evals").joinpath("questions.json").read_text())
    return [
        Question(
            id=q["id"],
            question=q["question"],
            expected_tool=q["expected_tool"],
            answer=float(q["answer"]),
            reference=[ReferenceCall(**r) for r in q["reference"]],
            tolerance=float(q.get("tolerance", 0.5)),
        )
        for q in raw
    ]


def extract(data: Any, path: str) -> float:
    """Follow a dotted path like 'overall.1.persons' through dicts and lists."""
    for part in path.split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return float(data)


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    ok: bool
    output: str
    ms: float


@dataclass
class ToolBox:
    """What a model client sees: tool specs, and a way to call them that keeps a log."""

    client: Client
    specs: list[dict[str, Any]] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)

    async def load(self) -> None:
        tools = (await self.client.list_tools()).tools
        self.specs = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in tools
        ]

    async def call(self, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
        """Returns (ok, structured result or error text)."""
        started = time.perf_counter()
        result = await self.client.call_tool(name, args)
        text = "".join(c.text for c in result.content if isinstance(c, TextContent))
        ok = not result.is_error
        payload: Any = (result.structured_content if ok else None) or text
        self.calls.append(
            ToolCall(name, args, ok, text[:2000], (time.perf_counter() - started) * 1000)
        )
        return ok, payload


@dataclass
class Answer:
    value: float | None
    explanation: str = ""


class ModelClient(Protocol):
    name: str

    async def answer(self, question: str, tools: ToolBox) -> Answer: ...


NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_number(text: str) -> float | None:
    """Pull the answer out of free text: JSON {"answer": n} first, else the last number."""
    match = re.search(r'\{[^{}]*"answer"\s*:\s*(-?\d+(?:\.\d+)?)[^{}]*\}', text)
    if match:
        return float(match.group(1))
    numbers = NUMBER.findall(text.replace(",", ""))
    return float(numbers[-1]) if numbers else None


@dataclass
class Outcome:
    question: Question
    answer: Answer
    calls: list[ToolCall]
    seconds: float

    @property
    def tool_selected(self) -> bool:
        return any(c.name == self.question.expected_tool and c.ok for c in self.calls)

    @property
    def correct(self) -> bool:
        value = self.answer.value
        return value is not None and abs(value - self.question.answer) <= self.question.tolerance

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.question.id,
            "expected": self.question.answer,
            "answer": self.answer.value,
            "correct": self.correct,
            "tool_selected": self.tool_selected,
            "calls": [{"tool": c.name, "args": c.args, "ok": c.ok} for c in self.calls],
            "failed_calls": sum(not c.ok for c in self.calls),
            "seconds": round(self.seconds, 3),
            "explanation": self.answer.explanation,
        }


@dataclass
class Report:
    model: str
    outcomes: list[Outcome]

    @property
    def accuracy(self) -> float:
        return sum(o.correct for o in self.outcomes) / max(len(self.outcomes), 1)

    @property
    def tool_selection(self) -> float:
        return sum(o.tool_selected for o in self.outcomes) / max(len(self.outcomes), 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "questions": len(self.outcomes),
            "accuracy": round(self.accuracy, 3),
            "tool_selection": round(self.tool_selection, 3),
            "tool_calls": sum(len(o.calls) for o in self.outcomes),
            "failed_tool_calls": sum(sum(not c.ok for c in o.calls) for o in self.outcomes),
            "results": [o.as_dict() for o in self.outcomes],
        }

    def table(self) -> str:
        lines = [f"{'question':<28} {'expected':>9} {'answer':>9}  tool  correct"]
        for o in self.outcomes:
            got = "-" if o.answer.value is None else f"{o.answer.value:g}"
            lines.append(
                f"{o.question.id:<28} {o.question.answer:>9g} {got:>9}  "
                f"{'yes ' if o.tool_selected else 'no  '}  {'yes' if o.correct else 'NO'}"
            )
        lines.append(
            f"\n{self.model}: {sum(o.correct for o in self.outcomes)}/{len(self.outcomes)} correct "
            f"({self.accuracy:.0%}), tool selection {self.tool_selection:.0%}, "
            f"{sum(len(o.calls) for o in self.outcomes)} tool calls"
        )
        return "\n".join(lines)


async def run_suite(client: Client, model: ModelClient, questions: list[Question]) -> Report:
    outcomes: list[Outcome] = []
    for question in questions:
        tools = ToolBox(client)
        await tools.load()
        started = time.perf_counter()
        try:
            answer = await model.answer(question.question, tools)
        except Exception as exc:  # a crashing client scores zero on that question, not the run
            answer = Answer(None, f"client error: {exc}")
        outcomes.append(Outcome(question, answer, tools.calls, time.perf_counter() - started))
    return Report(model.name, outcomes)
