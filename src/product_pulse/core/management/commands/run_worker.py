import signal
import socket
from typing import Any

import redis
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from product_pulse.clickhouse.client import default_client
from product_pulse.ingestion.buffer import RedisStreamBuffer
from product_pulse.ingestion.persons import PersonResolver
from product_pulse.ingestion.processor import EventProcessor
from product_pulse.ingestion.worker import Worker


class Command(BaseCommand):
    help = "Move buffered events from the Redis stream into ClickHouse."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--consumer", default=f"writer-{socket.gethostname()}")
        parser.add_argument("--read-count", type=int, default=50)

    def handle(self, *args: Any, **options: Any) -> None:
        source = RedisStreamBuffer(
            redis.Redis.from_url(settings.PULSE_REDIS_URL),
            stream=settings.PULSE_STREAM,
            consumer=options["consumer"],
        )
        source.ensure_group()
        worker = Worker(
            source,
            EventProcessor(PersonResolver(), default_client()),
            read_count=options["read_count"],
        )
        stop = False

        def request_stop(*_: object) -> None:
            nonlocal stop
            stop = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        self.stdout.write(f"worker {options['consumer']} reading {settings.PULSE_STREAM}")
        worker.run_forever(lambda: stop)
        self.stdout.write(
            f"stopped after {worker.stats.events} events, {worker.stats.failures} failed writes"
        )
