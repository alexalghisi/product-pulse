"""A small ClickHouse client over the HTTP interface.

Values never go into SQL text. Queries use ClickHouse's server-side parameters
(`{name:Type}` in the SQL, `param_name=value` in the request), so the server parses
each value as data of the declared type.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

import httpx

ParamValue = str | int | float | bool | date | datetime | UUID | Sequence[str] | Sequence[int]
Row = dict[str, Any]


class ClickHouseError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class QueryExecutor(Protocol):
    def query(self, sql: str, params: Mapping[str, ParamValue] | None = None) -> list[Row]: ...


def format_param(value: ParamValue) -> str:
    """Render a value in the text form ClickHouse expects for `param_*` arguments."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str | int | float | UUID):
        return str(value)
    return "[" + ",".join(_array_element(v) for v in value) + "]"


def _array_element(value: str | int) -> str:
    if isinstance(value, int):
        return str(value)
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


@dataclass(frozen=True)
class ClickHouseConfig:
    url: str
    database: str
    user: str = "default"
    password: str = ""
    timeout_s: float = 60.0


class ClickHouseClient:
    def __init__(self, config: ClickHouseConfig, http: httpx.Client | None = None) -> None:
        self.config = config
        # Credentials go in headers so they don't end up in access logs with the URL.
        self._http = http or httpx.Client(
            base_url=config.url,
            timeout=config.timeout_s,
            headers={"X-ClickHouse-User": config.user, "X-ClickHouse-Key": config.password},
        )

    def close(self) -> None:
        self._http.close()

    def _base_params(self, database: bool = True) -> dict[str, str]:
        params: dict[str, str] = {}
        if database:
            params["database"] = self.config.database
        return params

    def command(self, sql: str, *, use_database: bool = True) -> None:
        self._post(sql.encode(), self._base_params(use_database))

    def query(self, sql: str, params: Mapping[str, ParamValue] | None = None) -> list[Row]:
        request_params = self._base_params()
        for name, value in (params or {}).items():
            request_params[f"param_{name}"] = format_param(value)
        # output_format_json_quote_64bit_integers=0 keeps counts as JSON numbers.
        request_params["output_format_json_quote_64bit_integers"] = "0"
        body = (sql.rstrip().rstrip(";") + "\nFORMAT JSON").encode()
        response = self._post(body, request_params)
        payload: dict[str, Any] = response.json()
        rows: list[Row] = payload["data"]
        return rows

    def insert_json_rows(self, table: str, rows: Iterable[Mapping[str, Any]]) -> int:
        """Insert rows with JSONEachRow. `table` is a constant from our own code."""
        lines = [json.dumps(row, separators=(",", ":"), default=str) for row in rows]
        if not lines:
            return 0
        params = self._base_params()
        params["query"] = f"INSERT INTO {table} FORMAT JSONEachRow"
        params["date_time_input_format"] = "best_effort"
        self._post("\n".join(lines).encode(), params)
        return len(lines)

    def _post(self, body: bytes, params: Mapping[str, str]) -> httpx.Response:
        try:
            response = self._http.post("/", params=params, content=body)
        except httpx.HTTPError as exc:
            raise ClickHouseError(f"ClickHouse unreachable: {exc}") from exc
        if response.status_code != 200:
            code_header = response.headers.get("X-ClickHouse-Exception-Code")
            raise ClickHouseError(
                response.text.strip()[:2000],
                code=int(code_header) if code_header and code_header.isdigit() else None,
            )
        return response


def default_client() -> ClickHouseClient:
    from django.conf import settings

    return ClickHouseClient(
        ClickHouseConfig(
            url=settings.PULSE_CLICKHOUSE_URL, database=settings.PULSE_CLICKHOUSE_DATABASE
        )
    )
