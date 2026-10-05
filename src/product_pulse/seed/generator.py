"""Synthetic product data for a B2B SaaS app: marketing visits, sign-ups, activation, usage.

The shape is what makes the demo worth querying:

* traffic grows over the period and dips at weekends;
* visitors are anonymous until they sign up, then `$identify` links the two ids;
* an activation funnel (signed_up -> project_created -> insight_created ->
  invited_teammate -> subscription_started) loses people at every step, with delays
  long enough that the conversion window matters;
* conversion differs by browser and acquisition source, so breakdowns show something;
* usage decays after sign-up, so retention curves bend instead of being flat.

Generation runs day by day and yields each day's events in time order, so memory stays
flat however many days are generated, and identifies arrive after the anonymous events
they link, as they would live.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from product_pulse.ingestion.events import CapturedEvent

SOURCES = [
    ("google", 0.40),
    ("direct", 0.22),
    ("github", 0.15),
    ("twitter", 0.13),
    ("newsletter", 0.10),
]
SOURCE_SIGNUP = {"google": 1.0, "direct": 1.1, "github": 1.3, "twitter": 0.7, "newsletter": 1.6}
BROWSERS = [("Chrome", 0.62), ("Safari", 0.20), ("Firefox", 0.10), ("Edge", 0.08)]
# Firefox users activate less often: a rendering bug in the project wizard, say.
BROWSER_ACTIVATION = {"Chrome": 1.0, "Safari": 0.95, "Firefox": 0.7, "Edge": 1.0}
MARKETING_PAGES = ["/", "/pricing", "/docs", "/blog/funnels", "/blog/retention", "/customers"]
APP_PAGES = ["/insights", "/dashboards", "/events", "/insights/new", "/settings"]
QUERY_KINDS = [("trend", 0.6), ("funnel", 0.25), ("retention", 0.15)]
PLANS = [("pro", 49, 0.75), ("team", 199, 0.25)]


def _weighted(rng: random.Random, options: list[tuple[str, float]]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


@dataclass
class _User:
    user_id: str
    base: dict[str, Any]
    signed_up_at: datetime
    engagement: float


@dataclass(frozen=True)
class SeedConfig:
    days: int = 90
    end: datetime | None = None  # exclusive; defaults to the start of the next hour
    visitors_per_day: int = 1100
    seed: int = 42


class Generator:
    def __init__(self, project_id: int, config: SeedConfig) -> None:
        self.project_id = project_id
        self.config = config
        self.rng = random.Random(config.seed)
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        self.end = config.end or now
        self.start = (self.end - timedelta(days=config.days)).replace(hour=0)
        self.users: list[_User] = []
        self.later: dict[int, list[CapturedEvent]] = defaultdict(list)
        self._visitors = 0

    # -- helpers --------------------------------------------------------------------------

    def _event(
        self, distinct_id: str, name: str, at: datetime, props: dict[str, Any]
    ) -> CapturedEvent:
        return CapturedEvent(
            uuid=str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
            project_id=self.project_id,
            event=name,
            distinct_id=distinct_id,
            properties=props,
            timestamp=at,
        )

    def _emit(self, today: int, out: list[CapturedEvent], event: CapturedEvent) -> None:
        if event.timestamp >= self.end:
            return
        day = (event.timestamp - self.start).days
        if day <= today:
            out.append(event)
        else:
            self.later[day].append(event)

    def _daytime(self, day_start: datetime) -> datetime:
        hour = min(max(self.rng.gauss(14, 4), 0), 23.99)
        return day_start + timedelta(hours=hour, seconds=self.rng.random() * 60)

    def _delay(self, median_minutes: float, spread: float = 1.0) -> timedelta:
        return timedelta(minutes=median_minutes * math.exp(self.rng.gauss(0, spread)))

    # -- simulation ---------------------------------------------------------------------

    def _visitor(self, today: int, day_start: datetime, out: list[CapturedEvent]) -> None:
        rng = self.rng
        self._visitors += 1
        anon = f"anon-{self.config.seed}-{self._visitors}"
        source = _weighted(rng, SOURCES)
        browser = _weighted(rng, BROWSERS)
        mobile = rng.random() < 0.3
        if mobile:
            os_name = "iOS" if browser == "Safari" or rng.random() < 0.45 else "Android"
        else:
            os_name = rng.choices(["Mac OS X", "Windows", "Linux"], weights=[45, 45, 10])[0]
        base = {
            "$browser": browser,
            "$os": os_name,
            "$device_type": "Mobile" if mobile else "Desktop",
            "utm_source": source,
        }
        at = self._daytime(day_start)
        for _ in range(1 + min(int(rng.expovariate(1 / 2.2)), 12)):
            page = rng.choice(MARKETING_PAGES)
            self._emit(today, out, self._event(anon, "$pageview", at, {**base, "$pathname": page}))
            at += self._delay(1.5)

        signup_p = 0.2 * SOURCE_SIGNUP[source] * (0.6 if mobile else 1.0)
        if rng.random() >= signup_p:
            return
        user_id = f"user-{self.config.seed}-{self._visitors}"
        at += self._delay(2, 0.5)
        self._emit(today, out, self._event(user_id, "signed_up", at, {**base, "plan": "free"}))
        self._emit(
            today,
            out,
            self._event(
                user_id, "$identify", at + timedelta(seconds=1), {"$anon_distinct_id": anon}
            ),
        )
        user = _User(user_id, base, at, engagement=rng.betavariate(2, 2.5))
        self.users.append(user)
        self._activation(today, out, user, at)

    def _activation(self, today: int, out: list[CapturedEvent], user: _User, at: datetime) -> None:
        rng = self.rng
        boost = BROWSER_ACTIVATION[user.base["$browser"]]
        steps = [
            ("project_created", 0.72 * boost, 25.0),
            ("insight_created", 0.62, 60.0 * 20),
            ("invited_teammate", 0.38 * (0.6 + user.engagement), 60.0 * 24 * 3),
        ]
        reached = 0
        for name, probability, median in steps:
            if rng.random() >= probability:
                break
            at += self._delay(median)
            self._emit(today, out, self._event(user.user_id, name, at, dict(user.base)))
            reached += 1
        if rng.random() < (0.3 if reached == 3 else 0.03 * reached):
            plan, amount, _ = rng.choices(PLANS, weights=[w for _, _, w in PLANS])[0]
            at += self._delay(60 * 24 * 2)
            props = {**user.base, "plan": plan, "amount": amount}
            self._emit(today, out, self._event(user.user_id, "subscription_started", at, props))

    def _session(
        self, today: int, day_start: datetime, out: list[CapturedEvent], user: _User
    ) -> None:
        rng = self.rng
        at = self._daytime(day_start)
        for _ in range(3 + int(rng.expovariate(1 / (6 + 10 * user.engagement)))):
            roll = rng.random()
            if roll < 0.45:
                name, props = "$pageview", {"$pathname": rng.choice(APP_PAGES)}
            elif roll < 0.7:
                name, props = "insight_viewed", {"insight_kind": _weighted(rng, QUERY_KINDS)}
            elif roll < 0.85:
                kind = _weighted(rng, QUERY_KINDS)
                duration = int(80 * math.exp(rng.gauss(0, 0.8)) * (3 if kind == "funnel" else 1))
                name, props = "query_run", {"query_kind": kind, "duration_ms": duration}
            else:
                name, props = "dashboard_viewed", {"tiles": rng.randint(2, 12)}
            self._emit(today, out, self._event(user.user_id, name, at, {**user.base, **props}))
            at += self._delay(1.2)

    def _active(self, user: _User, day_start: datetime) -> bool:
        age = (day_start - user.signed_up_at).days
        if age < 1:
            return False
        weekend = 0.45 if day_start.weekday() >= 5 else 1.0
        probability = user.engagement * (0.6 * 0.92**age + 0.2) * weekend
        return self.rng.random() < probability

    def days(self) -> Iterator[list[CapturedEvent]]:
        for day in range(self.config.days):
            day_start = self.start + timedelta(days=day)
            if day_start >= self.end:
                break
            out = self.later.pop(day, [])
            growth = 1 + 0.6 * day / max(self.config.days - 1, 1)
            weekend = 0.6 if day_start.weekday() >= 5 else 1.0
            visitors = int(
                self.config.visitors_per_day * growth * weekend * self.rng.uniform(0.9, 1.1)
            )
            for user in self.users:
                if self._active(user, day_start):
                    self._session(day, day_start, out, user)
            for _ in range(visitors):
                self._visitor(day, day_start, out)
            out.sort(key=lambda e: e.timestamp)
            yield out
