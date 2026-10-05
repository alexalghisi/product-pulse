from typing import Any

from django.core.management.base import BaseCommand

from product_pulse.clickhouse.client import default_client
from product_pulse.clickhouse.schema import migrate


class Command(BaseCommand):
    help = "Create the ClickHouse database and tables if they don't exist."

    def handle(self, *args: Any, **options: Any) -> None:
        client = default_client()
        migrate(client)
        self.stdout.write(f"ClickHouse database {client.config.database!r} is up to date")
