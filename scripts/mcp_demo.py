"""Drive the MCP server over stdio the way an agent would, printing each call.

PULSE_API_KEY=ppk_... python scripts/mcp_demo.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import TextContent

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("list_events", {}),
    ("run_trend", {"series": [{"event": "signup"}], "date_from": "-30d"}),
    (
        "run_trend",
        {
            "series": [{"event": "signed_up", "math": "unique_persons"}],
            "date_from": "-30d",
            "interval": "week",
        },
    ),
    ("get_event_properties", {"event": "signed_up"}),
    (
        "run_funnel",
        {
            "steps": ["signed_up", "project_created", "insight_created", "invited_teammate"],
            "conversion_window": 14,
            "date_from": "-30d",
            "breakdown": "$browser",
        },
    ),
]


def compact(name: str, data: dict[str, Any]) -> str:
    if name == "list_events":
        return ", ".join(f"{e['name']} ({e['count']:,})" for e in data["events"][:6]) + ", ..."
    if name == "get_event_properties":
        return ", ".join(f"{p['key']} {p['examples'][:3]}" for p in data["properties"][:4])
    if name == "run_trend":
        return "; ".join(
            f"{s['event']} {s['math']}: total={s['total']:,.0f}" for s in data["series"]
        )
    if name == "run_funnel":
        rows = []
        for b in data.get("breakdowns", [])[:4]:
            steps = " > ".join(f"{s['persons']:,}" for s in b["steps"])
            rate = b["steps"][-1]["conversion_from_start"]
            rows.append(f"{b['breakdown_value']:<8} {steps}  ({rate:.1%})")
        return "\n  ".join(rows)
    return json.dumps(data)[:200]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "product_pulse.mcp_server"], env=dict(os.environ)
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        print(f"connected: {len(tools)} tools ({', '.join(t.name for t in tools)})\n")
        for name, args in CALLS:
            print(f"> {name}({json.dumps(args)})")
            result = await client.call_tool(name, args)
            if result.is_error:
                message = "".join(c.text for c in result.content if isinstance(c, TextContent))
                print(f"  error: {message}\n")
            else:
                print(f"  {compact(name, dict(result.structured_content or {}))}\n")


if __name__ == "__main__":
    anyio.run(main)
