"""A model client for any OpenAI-compatible Chat Completions endpoint with tool calling.

The MCP tool list is passed through as function definitions unchanged (name,
description, JSON schema), so what the model reads is exactly what an MCP host would
show it. The loop runs tool calls until the model answers or the turn budget runs out.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from product_pulse.evals.harness import Answer, ToolBox, parse_number

SYSTEM = """\
You answer product analytics questions using the tools provided. Use the tools to find
event names and properties before querying; never guess numbers. When you have the
answer, reply with only a JSON object: {"answer": <number>}. Percentages are numbers
from 0 to 100 (e.g. 41.5 for 41.5%)."""


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    max_turns: int = 8
    timeout_s: float = 120.0

    @classmethod
    def from_env(cls) -> OpenAIConfig | None:
        key = os.environ.get("PULSE_EVAL_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        return cls(
            api_key=key,
            model=os.environ.get("PULSE_EVAL_MODEL", cls.model),
            base_url=os.environ.get("PULSE_EVAL_BASE_URL", cls.base_url).rstrip("/"),
        )


class OpenAICompatibleClient:
    def __init__(self, config: OpenAIConfig, http: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.name = f"openai-compatible:{config.model}"
        self.http = http or httpx.AsyncClient(timeout=config.timeout_s)

    def _tools(self, tools: ToolBox) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["input_schema"],
                },
            }
            for spec in tools.specs
        ]

    async def _complete(self, messages: list[dict[str, Any]], tools: ToolBox) -> dict[str, Any]:
        response = await self.http.post(
            f"{self.config.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={
                "model": self.config.model,
                "messages": messages,
                "tools": self._tools(tools),
                "temperature": 0,
            },
        )
        response.raise_for_status()
        message: dict[str, Any] = response.json()["choices"][0]["message"]
        return message

    async def answer(self, question: str, tools: ToolBox) -> Answer:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
        ]
        for _ in range(self.config.max_turns):
            message = await self._complete(messages, tools)
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                content = message.get("content") or ""
                return Answer(parse_number(content), content)
            for call in calls:
                function = call["function"]
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    output = f"Arguments were not valid JSON: {exc}"
                else:
                    ok, result = await tools.call(function["name"], args)
                    output = json.dumps(result) if ok else f"Error: {result}"
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
        return Answer(None, f"no answer after {self.config.max_turns} turns")
