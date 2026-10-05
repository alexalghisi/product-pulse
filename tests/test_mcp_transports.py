"""stdio and streamable HTTP, each in its own process, the way a client would launch them."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import anyio
import httpx
import httpx2
import pytest
from django.contrib.auth.models import User
from django.db import connection
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.core.models import PersonalAPIKey, Project

from .dataset import load, reference_events

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.clickhouse]


def server_env(clickhouse: ClickHouseClient) -> dict[str, str]:
    db = connection.settings_dict
    return {
        **os.environ,
        "PULSE_DATABASE_URL": f"postgres://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}",
        "PULSE_CLICKHOUSE_URL": clickhouse.config.url,
        "PULSE_CLICKHOUSE_DATABASE": clickhouse.config.database,
        "PULSE_LOG_LEVEL": "WARNING",
    }


@pytest.fixture
def key(clickhouse: ClickHouseClient, project: Project) -> str:
    load(clickhouse, reference_events(project.pk))
    user = User.objects.create_user("agent")
    return PersonalAPIKey.issue(user=user, project=project, label="agent")[1]


def event_names(result: Any) -> list[str]:
    assert not result.is_error
    return [e["name"] for e in result.structured_content["events"]]


def test_stdio(clickhouse: ClickHouseClient, key: str) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "product_pulse.mcp_server"],
        env={**server_env(clickhouse), "PULSE_API_KEY": key},
    )

    async def main() -> list[str]:
        async with Client(params) as client:
            return event_names(await client.call_tool("list_events", {}))

    assert "$pageview" in anyio.run(main)


@pytest.fixture
def http_server(clickhouse: ClickHouseClient) -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "product_pulse.mcp_server",
            "--transport",
            "http",
            "--port",
            str(port),
        ],
        env=server_env(clickhouse),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    yield url
    process.terminate()
    process.wait(timeout=10)


def test_streamable_http_requires_a_personal_key(http_server: str, key: str) -> None:
    async def main(token: str) -> list[str]:
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
        async with Client(streamable_http_client(http_server, http_client=http)) as client:
            return event_names(await client.call_tool("list_events", {}))

    assert "signed_up" in anyio.run(main, key)
    assert (
        httpx.post(http_server, json={}, headers={"Authorization": "Bearer ppk_wrong"}).status_code
        == 401
    )
    assert httpx.post(http_server, json={}).status_code == 401
