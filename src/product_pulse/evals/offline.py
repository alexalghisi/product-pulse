"""A deterministic, rule-based planner so the eval suite runs in CI without a model.

It is a baseline, not an agent. It discovers events and properties through the tools
like a model would, then maps keywords to one query:

* dates: "June 2026", "May 2026", "the week starting 4 May 2026";
* query kind: "within N days" / "percentage of people who ... then" -> funnel,
  "following week" / "weeks after" -> retention, anything else -> trend;
* events: event names split into words and matched against the question, with a few
  hand-written phrasings ("viewed a page" -> $pageview);
* math: "how many people/users" -> unique persons, "total ... amount" -> sum,
  "average" -> avg, otherwise a count;
* filters: any known property value that appears verbatim in the question.

What it cannot do, by design: paraphrases whose words don't match an event name
("send a note to someone else"), questions that need two queries and arithmetic,
reasoning over a breakdown ("the source with the most sign-ups"), or recovering from an
error. Those questions are in the suite on purpose, and the offline score reports them
as misses.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from product_pulse.evals.harness import Answer, ToolBox

_MONTH_NAMES = (
    "january february march april may june july august september october november december"
)
MONTHS = {m: i + 1 for i, m in enumerate(_MONTH_NAMES.split())}
PHRASES = {
    "$pageview": ["viewed a page", "pageview", "page view"],
    "signed_up": ["signed up", "sign-up", "signup", "sign up"],
}


def _stem(word: str) -> str:
    word = word.lower().strip("$")
    for suffix in ("ing", "ed", "es", "s", "e"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)]
    return word


def _month_range(text: str) -> tuple[str, str] | None:
    match = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(\d{4})\b", text.lower())
    if not match:
        return None
    month, year = MONTHS[match.group(1)], int(match.group(2))
    first = date(year, month, 1)
    last = (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


def _week_range(text: str) -> tuple[str, str] | None:
    match = re.search(r"week starting (\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})", text.lower())
    if not match:
        return None
    start = date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1)))
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def mentioned_events(question: str, names: list[str]) -> list[str]:
    """Events whose words all appear in the question, in order of first mention."""
    lowered = question.lower()
    tokens = [(m.start(), _stem(m.group())) for m in re.finditer(r"[a-zA-Z$]+", question)]
    first: dict[str, int] = {}
    for position, stem in tokens:
        first.setdefault(stem, position)
    found: list[tuple[int, str]] = []
    for name in names:
        positions = [lowered.find(p) for p in PHRASES.get(name, []) if p in lowered]
        parts = [_stem(p) for p in name.strip("$").split("_") if p]
        if parts and all(p in first for p in parts):
            positions.append(min(first[p] for p in parts))
        if positions:
            found.append((min(positions), name))
    return [name for _, name in sorted(found)]


class OfflinePlanner:
    name = "offline-planner"

    async def answer(self, question: str, tools: ToolBox) -> Answer:
        ok, listing = await tools.call("list_events", {})
        if not ok:
            return Answer(None, "list_events failed")
        names = [e["name"] for e in listing["events"] if e["name"] != "$identify"]
        events = mentioned_events(question, names)
        if not events:
            return Answer(None, "no event recognised in the question")
        dates = _week_range(question) or _month_range(question)
        date_args: dict[str, Any] = {"date_from": dates[0], "date_to": dates[1]} if dates else {}
        q = question.lower()
        percent = any(w in q for w in ("percentage", "percent", "rate", "what share"))

        if re.search(r"following week|weeks? after", q):
            return await self._retention(tools, q, events, date_args, percent)
        window = re.search(r"within (\d+|a) (day|week)s?", q)
        if window and len(events) >= 2:
            return await self._funnel(tools, q, events, window, date_args, percent)
        return await self._trend(tools, q, events[0], date_args)

    async def _filters(self, tools: ToolBox, question: str, event: str) -> list[dict[str, Any]]:
        ok, props = await tools.call("get_event_properties", {"event": event})
        if not ok:
            return []
        filters = []
        for prop in props["properties"]:
            for value in prop["examples"]:
                if len(value) > 2 and re.search(rf"\b{re.escape(value.lower())}\b", question):
                    filters.append({"key": prop["key"], "value": value})
        return filters

    async def _trend(self, tools: ToolBox, q: str, event: str, dates: dict[str, Any]) -> Answer:
        series: dict[str, Any] = {"event": event}
        if "average" in q or "total revenue" in q or "sum of" in q:
            ok, props = await tools.call("get_event_properties", {"event": event})
            numeric = (
                [
                    p["key"]
                    for p in props["properties"]
                    if p["type"] in {"int64", "uint64", "double"}
                ]
                if ok
                else []
            )
            if not numeric:
                return Answer(None, f"no numeric property on {event}")
            series.update(math="avg" if "average" in q else "sum", math_property=numeric[0])
        elif re.search(
            r"how many (unique )?(people|users|persons)|unique users|how many \w+ users", q
        ):
            series["math"] = "unique_persons"
        args: dict[str, Any] = {"series": [series], **dates}
        if filters := await self._filters(tools, q, event):
            args["filters"] = filters
        ok, result = await tools.call("run_trend", args)
        if not ok:
            return Answer(None, str(result))
        return Answer(float(result["series"][0]["total"]), f"run_trend {args}")

    async def _funnel(
        self,
        tools: ToolBox,
        q: str,
        events: list[str],
        window: re.Match[str],
        dates: dict[str, Any],
        percent: bool,
    ) -> Answer:
        amount = 1 if window.group(1) == "a" else int(window.group(1))
        args: dict[str, Any] = {
            "steps": events[:2],
            "conversion_window": amount,
            "conversion_window_unit": window.group(2),
            **dates,
        }
        if filters := await self._filters(tools, q, events[0]):
            args["filters"] = filters
        ok, result = await tools.call("run_funnel", args)
        if not ok:
            return Answer(None, str(result))
        last = result["overall"][-1]
        value = last["conversion_from_start"] * 100 if percent else last["persons"]
        return Answer(round(float(value), 2), f"run_funnel {args}")

    async def _retention(
        self, tools: ToolBox, q: str, events: list[str], dates: dict[str, Any], percent: bool
    ) -> Answer:
        if len(events) < 2:
            return Answer(None, "retention needs a start and a return event")
        offset = 2 if re.search(r"two weeks|2 weeks", q) else 1
        args = {
            "start_event": events[0],
            "return_event": events[1],
            "period": "week",
            "periods": max(offset, 4),
            **dates,
        }
        ok, result = await tools.call("run_retention", args)
        if not ok or not result["cohorts"]:
            return Answer(None, str(result))
        cohort = result["cohorts"][0]
        value = cohort["rates"][offset - 1] * 100 if percent else cohort["retained"][offset - 1]
        return Answer(round(float(value), 2), f"run_retention {args}")
