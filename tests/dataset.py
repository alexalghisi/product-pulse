"""A small dataset whose answers are worked out by hand, loaded through the real writer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.ingestion.events import CapturedEvent
from product_pulse.ingestion.persons import PersonResolver
from product_pulse.ingestion.processor import EventProcessor


def at(day: int, hour: int = 10, month: int = 6) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def ev(
    project_id: int, distinct_id: str, event: str, when: datetime, /, **props: Any
) -> CapturedEvent:
    return CapturedEvent(str(uuid4()), project_id, event, distinct_id, props, when)


def reference_events(project_id: int) -> list[CapturedEvent]:
    p = project_id
    return [
        # $pageview: day 1 -> 4, day 2 -> 2, day 3 -> 1. Persons: u1, u2, u3 (= anon-3).
        ev(p, "u1", "$pageview", at(1, 9), **{"$browser": "Chrome"}),
        ev(p, "u1", "$pageview", at(1, 10), **{"$browser": "Chrome"}),
        ev(p, "u1", "$pageview", at(1, 11), **{"$browser": "Chrome"}),
        ev(p, "u2", "$pageview", at(1, 12), **{"$browser": "Firefox"}),
        ev(p, "u2", "$pageview", at(2, 9), **{"$browser": "Firefox"}),
        ev(p, "anon-3", "$pageview", at(2, 9), **{"$browser": "Safari"}),
        ev(p, "u3", "$pageview", at(3, 9)),
        # Purchases: 10 + 20.5 + "5" = 35.5
        ev(p, "u1", "purchase", at(4), amount=10),
        ev(p, "u1", "purchase", at(5), amount=20.5),
        ev(p, "u2", "purchase", at(5), amount="5"),
        # Funnel signed_up -> project_created -> invited_teammate
        ev(p, "u1", "signed_up", at(1, 10), plan="pro"),
        ev(p, "u1", "project_created", at(1, 11)),
        ev(p, "u1", "invited_teammate", at(1, 12)),
        ev(p, "u2", "signed_up", at(1, 10), plan="free"),
        ev(p, "u2", "project_created", at(3, 11)),  # 49h later: outside a 1-day window
        ev(p, "anon-3", "signed_up", at(2, 10), plan="free"),
        ev(p, "u3", "$identify", at(2, 10).replace(minute=30), **{"$anon_distinct_id": "anon-3"}),
        ev(p, "u3", "project_created", at(2, 11)),
        ev(p, "u4", "project_created", at(2, 11)),  # never signed up
        ev(p, "u5", "signed_up", at(31, 10, month=5), plan="pro"),  # before the range
        # Retention on app_opened, daily
        ev(p, "r1", "app_opened", at(1)),
        ev(p, "r1", "app_opened", at(2)),
        ev(p, "r1", "app_opened", at(3)),
        ev(p, "r2", "app_opened", at(1)),
        ev(p, "r2", "app_opened", at(3)),
        ev(p, "r3", "app_opened", at(2)),
        ev(p, "r3", "app_opened", at(4)),
    ]


def noise_events(project_id: int) -> list[CapturedEvent]:
    """Same names and distinct_ids, different numbers: for tenant isolation tests."""
    p = project_id
    out = reference_events(p)
    for i in range(50):
        out.append(
            ev(
                p,
                f"u{i % 7}",
                "$pageview",
                at(1 + i % 5),
                **{"$browser": "Chrome", "project_id": p},
            )
        )
        out.append(ev(p, f"x{i}", "signed_up", at(1 + i % 5), plan="pro"))
        out.append(ev(p, f"x{i}", "project_created", at(1 + i % 5, 11)))
    out.append(ev(p, "anon-3", "$identify", at(1), **{"$anon_distinct_id": "u1"}))
    return out


def load(clickhouse: ClickHouseClient, events: list[CapturedEvent]) -> None:
    processor = EventProcessor(PersonResolver(), clickhouse)
    # Two writes, so the identify arrives after the anonymous events like it would live.
    processor.process(events[: len(events) // 2])
    processor.process(events[len(events) // 2 :])
