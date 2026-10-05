"""Ingestion throughput: HTTP capture into Redis, then the writer from Redis into ClickHouse.

    python bench/ingest.py [--events 200000] [--batch 100] [--concurrency 4] [--workers 2]

Starts gunicorn on 127.0.0.1:8141 with its own stream, posts gzip'd batches built from
the demo generator, then drains the stream with one writer and checks the row count.
Client, server, Redis, Postgres and ClickHouse all share the same machine.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from _django import setup, summary_ms

STREAM = "pulse:bench"
PORT = 8141


def build_bodies(project_id: int, token: str, total: int, batch: int) -> list[bytes]:
    from product_pulse.seed.generator import Generator, SeedConfig

    events: list[dict[str, Any]] = []
    for day in Generator(project_id, SeedConfig(days=30, visitors_per_day=1500, seed=7)).days():
        for e in day:
            events.append(
                {
                    "event": e.event,
                    "distinct_id": e.distinct_id,
                    "properties": e.properties,
                    "timestamp": e.timestamp.isoformat(),
                    "uuid": e.uuid,
                }
            )
            if len(events) >= total:
                break
        if len(events) >= total:
            break
    return [
        gzip.compress(json.dumps({"api_key": token, "batch": events[i : i + batch]}).encode())
        for i in range(0, len(events), batch)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=200_000)
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--workers", type=int, default=2, help="gunicorn workers")
    args = parser.parse_args()

    setup()
    import redis
    from django.conf import settings

    from product_pulse.clickhouse.client import default_client
    from product_pulse.core.models import Project
    from product_pulse.ingestion.buffer import RedisStreamBuffer
    from product_pulse.ingestion.persons import PersonResolver
    from product_pulse.ingestion.processor import EventProcessor
    from product_pulse.ingestion.worker import Worker

    project = Project.objects.create(name=f"bench {time.strftime('%Y-%m-%d %H:%M:%S')}")
    redis_client = redis.Redis.from_url(settings.PULSE_REDIS_URL)
    redis_client.delete(STREAM)
    bodies = build_bodies(project.pk, project.api_token, args.events, args.batch)
    sent_events = args.events

    env = {
        **os.environ,
        "PULSE_STREAM": STREAM,
        "PULSE_STREAM_HIGH_WATERMARK": "10000000",
        "PULSE_DEBUG": "0",
        "PULSE_LOG_LEVEL": "WARNING",
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gunicorn",
            "product_pulse.wsgi",
            "-w",
            str(args.workers),
            "-b",
            f"127.0.0.1:{PORT}",
            "--log-level",
            "warning",
        ],
        env=env,
    )
    try:
        url = f"http://127.0.0.1:{PORT}/capture"
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)

        latencies: list[float] = []
        accepted = 0

        def post(body: bytes, http: httpx.Client) -> int:
            started = time.perf_counter()
            response = http.post(
                url,
                content=body,
                headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
            )
            latencies.append(time.perf_counter() - started)
            response.raise_for_status()
            return int(response.json()["accepted"])

        clients = [httpx.Client(timeout=30) for _ in range(args.concurrency)]
        started = time.perf_counter()
        with ThreadPoolExecutor(args.concurrency) as pool:
            futures = [
                pool.submit(post, b, clients[i % args.concurrency]) for i, b in enumerate(bodies)
            ]
            accepted = sum(f.result() for f in futures)
        capture_s = time.perf_counter() - started
    finally:
        server.terminate()
        server.wait(timeout=20)

    buffer = RedisStreamBuffer(redis_client, stream=STREAM, group="bench", consumer="bench-1")
    buffer.ensure_group()
    clickhouse = default_client()
    worker = Worker(
        buffer, EventProcessor(PersonResolver(), clickhouse), read_count=50, block_ms=200
    )
    started = time.perf_counter()
    while worker.run_once():
        pass
    drain_s = time.perf_counter() - started
    rows = clickhouse.query(
        "SELECT count() AS n FROM events FINAL WHERE project_id = {p:UInt64}", {"p": project.pk}
    )[0]["n"]
    redis_client.delete(STREAM)

    result = {
        "machine": f"{os.cpu_count()} vCPU, {platform.system()}, shared with other workloads",
        "events_sent": sent_events,
        "batch_size": args.batch,
        "concurrency": args.concurrency,
        "gunicorn_workers": args.workers,
        "capture": {
            "accepted": accepted,
            "seconds": round(capture_s, 2),
            "events_per_s": round(accepted / capture_s),
            "request_latency": summary_ms(latencies),
        },
        "writer": {
            "events": worker.stats.events,
            "seconds": round(drain_s, 2),
            "events_per_s": round(worker.stats.events / drain_s),
            "failures": worker.stats.failures,
        },
        "rows_in_clickhouse": rows,
    }
    out = Path(__file__).parent / "results" / "ingest.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
