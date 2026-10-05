from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest
import redis
from django.conf import settings

from product_pulse.clickhouse.client import ClickHouseClient, ClickHouseConfig
from product_pulse.clickhouse.schema import drop_database, migrate
from product_pulse.core.models import Project

CLICKHOUSE_URL = os.environ.get("PULSE_TEST_CLICKHOUSE_URL", "http://127.0.0.1:8130")
CLICKHOUSE_TEST_DATABASE = f"pulse_test_{os.getpid()}"
REDIS_URL = os.environ.get("PULSE_TEST_REDIS_URL", "redis://localhost:6379/13")


def _clickhouse_available() -> bool:
    try:
        return httpx.get(f"{CLICKHOUSE_URL}/ping", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def clickhouse_session() -> Iterator[ClickHouseClient]:
    if not _clickhouse_available():
        pytest.skip(f"ClickHouse not reachable at {CLICKHOUSE_URL} (scripts/dev-clickhouse.sh)")
    client = ClickHouseClient(
        ClickHouseConfig(url=CLICKHOUSE_URL, database=CLICKHOUSE_TEST_DATABASE)
    )
    migrate(client)
    yield client
    drop_database(client)
    client.close()


@pytest.fixture
def clickhouse(clickhouse_session: ClickHouseClient) -> Iterator[ClickHouseClient]:
    for table in ("events", "person_distinct_id"):
        clickhouse_session.command(f"TRUNCATE TABLE {table}")
    previous = (settings.PULSE_CLICKHOUSE_URL, settings.PULSE_CLICKHOUSE_DATABASE)
    settings.PULSE_CLICKHOUSE_URL = CLICKHOUSE_URL
    settings.PULSE_CLICKHOUSE_DATABASE = CLICKHOUSE_TEST_DATABASE
    yield clickhouse_session
    settings.PULSE_CLICKHOUSE_URL, settings.PULSE_CLICKHOUSE_DATABASE = previous


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(REDIS_URL)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    yield client
    client.close()


@pytest.fixture
def stream_name(redis_client: redis.Redis) -> Iterator[str]:
    name = f"pulse-test:{uuid.uuid4().hex}"
    yield name
    redis_client.delete(name, f"{name}:dead")


@pytest.fixture
def project(db: None) -> Project:
    return Project.objects.create(name="Test project")


@pytest.fixture
def other_project(db: None) -> Project:
    return Project.objects.create(name="Someone else's project")
