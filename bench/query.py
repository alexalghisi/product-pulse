"""Query latency on the seeded demo project (manage.py seed_demo).

    python bench/query.py [--project Demo] [--runs 15]

Times AnalyticsService.run, which is what the API and the MCP tools call: name
validation, SQL compilation, ClickHouse execution and result shaping. HTTP overhead is
not included. Every query covers the full 90 days unless noted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from _django import setup, summary_ms

FUNNEL = ["signed_up", "project_created", "insight_created", "invited_teammate"]
QUERIES: dict[str, dict[str, Any]] = {
    "trend: daily pageviews": {
        "kind": "trend",
        "series": [{"event": "$pageview"}],
        "date_range": {"date_from": "-90d"},
    },
    "trend: daily unique persons": {
        "kind": "trend",
        "series": [{"event": "$pageview", "math": "unique_persons"}],
        "date_range": {"date_from": "-90d"},
    },
    "trend: pageviews by $browser": {
        "kind": "trend",
        "series": [{"event": "$pageview"}],
        "breakdown": "$browser",
        "date_range": {"date_from": "-90d"},
    },
    "trend: hourly, last 7 days": {
        "kind": "trend",
        "series": [{"event": "insight_viewed"}],
        "interval": "hour",
        "date_range": {"date_from": "-7d"},
    },
    "funnel: 4 steps, 14 day window": {
        "kind": "funnel",
        "steps": [{"event": e} for e in FUNNEL],
        "date_range": {"date_from": "-90d"},
    },
    "funnel: 4 steps by $browser": {
        "kind": "funnel",
        "steps": [{"event": e} for e in FUNNEL],
        "breakdown": "$browser",
        "date_range": {"date_from": "-90d"},
    },
    "retention: weekly, 12 cohorts": {
        "kind": "retention",
        "start_event": {"event": "signed_up"},
        "return_event": {"event": "insight_viewed"},
        "period": "week",
        "periods": 8,
        "date_range": {"date_from": "-12w"},
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="Demo")
    parser.add_argument("--runs", type=int, default=15)
    args = parser.parse_args()
    setup()
    from product_pulse.analytics.service import AnalyticsService, parse_query
    from product_pulse.clickhouse.client import default_client
    from product_pulse.core.models import Project

    project = Project.objects.filter(name=args.project).order_by("pk").first()
    if project is None:
        raise SystemExit(f"no project named {args.project!r}; run manage.py seed_demo first")
    clickhouse = default_client()
    total = clickhouse.query(
        "SELECT count() AS n FROM events FINAL WHERE project_id = {p:UInt64}", {"p": project.pk}
    )[0]["n"]

    results: dict[str, Any] = {}
    for name, raw in QUERIES.items():
        query = parse_query(raw)
        timings: list[float] = []
        for i in range(args.runs + 2):
            service = AnalyticsService(project.pk, clickhouse)  # fresh: no discovery cache
            started = time.perf_counter()
            service.run(query)
            if i >= 2:  # two warm-up runs
                timings.append(time.perf_counter() - started)
        results[name] = summary_ms(timings)
        p50, p95 = results[name]["p50_ms"], results[name]["p95_ms"]
        print(f"{name:<34} p50 {p50:>7.1f} ms   p95 {p95:>7.1f} ms")

    out = {
        "machine": f"{os.cpu_count()} vCPU, shared with other workloads; ClickHouse max_threads=2",
        "events_in_project": total,
        "runs_per_query": args.runs,
        "latency": results,
    }
    (Path(__file__).parent / "results" / "query.json").write_text(json.dumps(out, indent=2) + "\n")
    print(f"\n{total:,} events in project {project.pk}")


if __name__ == "__main__":
    main()
