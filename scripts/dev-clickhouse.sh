#!/usr/bin/env bash
# Starts a local ClickHouse on 127.0.0.1:8130 (HTTP) / 9130 (native), data in ./.data/clickhouse.
set -euo pipefail
cd "$(dirname "$0")/.."
CLICKHOUSE_BIN="${CLICKHOUSE_BIN:-clickhouse}"
mkdir -p .data/clickhouse
exec "$CLICKHOUSE_BIN" server --config-file=deploy/clickhouse/config.xml
