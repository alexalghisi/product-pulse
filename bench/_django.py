"""Shared setup for the benchmark scripts."""

from __future__ import annotations

import os
import statistics


def setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "product_pulse.settings")
    os.environ.setdefault("PULSE_LOG_LEVEL", "WARNING")
    import django

    django.setup()


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def summary_ms(seconds: list[float]) -> dict[str, float]:
    ms = [s * 1000 for s in seconds]
    return {
        "p50_ms": round(statistics.median(ms), 1),
        "p95_ms": round(percentile(ms, 95), 1),
        "max_ms": round(max(ms), 1),
    }
