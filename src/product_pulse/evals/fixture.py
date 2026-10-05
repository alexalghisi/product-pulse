"""The eval dataset: a small notes app whose numbers are known by construction.

Expected answers in questions.json come from the rules below, not from running the
query engine, so the evals check the engine as well as the agent. The rules:

June 2026 users u1..u200 (index i):
* each first views a page anonymously (anon-i, 09:00 on their signup day), then signs
  up as u-i at 10:00 on June (1 + (i-1) % 30) and $identify links the two ids;
* after signing up they view 1 + i % 5 pages that day                 -> 600 + 200 anon
* $browser: i % 10 in 0..5 Chrome (120), 6..7 Firefox (40), 8..9 Safari (40)
* utm_source: i % 4 -> google, twitter, newsletter, direct (50 each)
* note_created one hour after signup if i % 10 in {0,1,2,3,4,6,8}      -> 140
  (Chrome 100, Firefox 20, Safari 20)
* note_shared 2 days after signup if i % 10 in {0, 1}                  -> 40 (within 7 days)
  and 10 days after signup if i % 10 == 6                              -> 20 (within 14 days)
* upgraded if i % 10 == 0 (20 people), amount 10 if i % 20 == 0 else 30 -> sum 400, avg 20
* export_clicked once each for i <= 100, format "pdf" if i % 10 < 7 else "csv" -> 70 / 30

May 2026 users m1..m100 (index j):
* sign up on May 4 + (j-1) % 7 for j <= 50 (week of Mon May 4), May 11 + (j-51) % 7
  for the rest (week of May 11); two pageviews on their signup day     -> 200
* app_opened 7 days after signup for j <= 30 and j in 51..60, and 14 days after
  signup for j <= 20.  Cohort May 4: 50 people, week 1: 30, week 2: 20.
  Cohort May 11: 50 people, week 1: 10, week 2: 0.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from product_pulse.ingestion.events import CapturedEvent

EVAL_PROJECT_NAME = "evals: notes app"


def _browser(i: int) -> str:
    r = i % 10
    return "Chrome" if r <= 5 else "Firefox" if r <= 7 else "Safari"


def _source(i: int) -> str:
    return ["google", "twitter", "newsletter", "direct"][i % 4]


def events(project_id: int) -> list[CapturedEvent]:
    out: list[CapturedEvent] = []

    def add(distinct_id: str, event: str, at: datetime, props: dict[str, Any]) -> None:
        # Stable uuids: loading the fixture twice is a no-op in ClickHouse.
        uid = uuid5(NAMESPACE_URL, f"pulse-eval/{distinct_id}/{event}/{at.isoformat()}")
        out.append(CapturedEvent(str(uid), project_id, event, distinct_id, props, at))

    for i in range(1, 201):
        day = datetime(2026, 6, 1 + (i - 1) % 30, tzinfo=UTC)
        user, anon = f"u-{i}", f"anon-{i}"
        base = {"$browser": _browser(i), "utm_source": _source(i)}
        signup = day + timedelta(hours=10, seconds=i)
        add(anon, "$pageview", day + timedelta(hours=9, seconds=i), {**base, "$pathname": "/"})
        add(user, "signed_up", signup, base)
        add(user, "$identify", signup + timedelta(seconds=1), {"$anon_distinct_id": anon})
        for n in range(1 + i % 5):
            add(
                user,
                "$pageview",
                signup + timedelta(minutes=5 + n),
                {**base, "$pathname": "/notes"},
            )
        if i % 10 in {0, 1, 2, 3, 4, 6, 8}:
            add(user, "note_created", signup + timedelta(hours=1), base)
        if i % 10 in {0, 1}:
            add(user, "note_shared", signup + timedelta(days=2), base)
        if i % 10 == 6:
            add(user, "note_shared", signup + timedelta(days=10), base)
        if i % 10 == 0:
            add(
                user,
                "upgraded",
                signup + timedelta(hours=3),
                {**base, "amount": 10 if i % 20 == 0 else 30},
            )
        if i <= 100:
            fmt = "pdf" if i % 10 < 7 else "csv"
            add(user, "export_clicked", signup + timedelta(hours=2), {**base, "format": fmt})

    for j in range(1, 101):
        start = datetime(2026, 5, 4, tzinfo=UTC) if j <= 50 else datetime(2026, 5, 11, tzinfo=UTC)
        offset = (j - 1) % 7 if j <= 50 else (j - 51) % 7
        signup = start + timedelta(days=offset, hours=11, seconds=j)
        user = f"m-{j}"
        base = {"$browser": _browser(j), "utm_source": _source(j)}
        add(user, "signed_up", signup, base)
        for n in range(2):
            add(user, "$pageview", signup + timedelta(minutes=1 + n), {**base, "$pathname": "/"})
        if j <= 30 or 51 <= j <= 60:
            add(user, "app_opened", signup + timedelta(days=7), base)
        if j <= 20:
            add(user, "app_opened", signup + timedelta(days=14), base)

    out.sort(key=lambda e: e.timestamp)
    return out


def batches(project_id: int, size: int = 500) -> Iterator[list[CapturedEvent]]:
    all_events = events(project_id)
    for start in range(0, len(all_events), size):
        yield all_events[start : start + size]
