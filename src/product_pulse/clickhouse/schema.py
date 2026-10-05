"""ClickHouse tables. Applied idempotently by `manage.py clickhouse_migrate`."""

from __future__ import annotations

import re

from product_pulse.clickhouse.client import ClickHouseClient

# The sorting key doubles as the deduplication key: a retried event carries the same
# uuid, timestamp, event and distinct_id, so ReplacingMergeTree collapses the copies and
# queries read `FINAL`. Leading with project_id and the day keeps every query, which is
# always scoped to one project and a date range, to a narrow slice of the table.
EVENTS = """
CREATE TABLE IF NOT EXISTS events
(
    uuid UUID,
    project_id UInt64,
    event LowCardinality(String),
    distinct_id String,
    properties String CODEC(ZSTD(3)),
    timestamp DateTime64(3, 'UTC'),
    inserted_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (project_id, toDate(timestamp), event, cityHash64(distinct_id), uuid)
"""

# distinct_id -> person. A merge inserts a row with a higher version instead of
# rewriting events, so a person's earlier anonymous events count for them immediately.
PERSON_DISTINCT_ID = """
CREATE TABLE IF NOT EXISTS person_distinct_id
(
    project_id UInt64,
    distinct_id String,
    person_id UUID,
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, distinct_id)
"""

TABLES = (EVENTS, PERSON_DISTINCT_ID)


def _database_name(client: ClickHouseClient) -> str:
    name = client.config.database
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
        raise ValueError(f"invalid ClickHouse database name: {name!r}")
    return name


def migrate(client: ClickHouseClient) -> None:
    _database_name(client)
    client.command(f"CREATE DATABASE IF NOT EXISTS `{client.config.database}`", use_database=False)
    for ddl in TABLES:
        client.command(ddl)


def drop_database(client: ClickHouseClient) -> None:
    _database_name(client)
    client.command(f"DROP DATABASE IF EXISTS `{client.config.database}`", use_database=False)
