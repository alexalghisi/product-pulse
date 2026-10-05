from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise

from product_pulse.seed.generator import Generator, SeedConfig

END = datetime(2026, 6, 30, tzinfo=UTC)


def test_generation_is_deterministic_and_in_time_order() -> None:
    first = Generator(1, SeedConfig(days=5, end=END, visitors_per_day=50))
    second = Generator(1, SeedConfig(days=5, end=END, visitors_per_day=50))
    a = [e for day in first.days() for e in day]
    b = [e for day in second.days() for e in day]
    assert a == b
    assert [e.timestamp for e in a] == sorted(e.timestamp for e in a)
    assert all(e.timestamp < END for e in a)


def test_funnel_drops_off_at_every_step() -> None:
    events = [
        e
        for day in Generator(1, SeedConfig(days=20, end=END, visitors_per_day=200)).days()
        for e in day
    ]
    counts = Counter(e.event for e in events)
    steps = ["signed_up", "project_created", "insight_created", "invited_teammate"]
    assert all(counts[a] > counts[b] > 0 for a, b in pairwise(steps))


def test_identify_links_each_signup_to_an_earlier_anonymous_id() -> None:
    events = [
        e
        for day in Generator(1, SeedConfig(days=3, end=END, visitors_per_day=100)).days()
        for e in day
    ]
    first_seen: dict[str, datetime] = {}
    for e in events:
        first_seen.setdefault(e.distinct_id, e.timestamp)
    identifies = [e for e in events if e.event == "$identify"]
    assert identifies
    for e in identifies:
        anon = e.properties["$anon_distinct_id"]
        assert first_seen[anon] < e.timestamp
