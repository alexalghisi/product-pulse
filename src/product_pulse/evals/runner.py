"""Wiring for the eval suite: the eval project and an in-process MCP session."""

from __future__ import annotations

from mcp import Client

from product_pulse.clickhouse.client import ClickHouseClient
from product_pulse.core.models import Project
from product_pulse.evals import fixture
from product_pulse.evals.harness import ModelClient, Question, Report, run_suite
from product_pulse.ingestion.persons import PersonResolver
from product_pulse.ingestion.processor import EventProcessor
from product_pulse.mcp_server.server import build_server


def load_fixture(project_id: int, clickhouse: ClickHouseClient) -> int:
    processor = EventProcessor(PersonResolver(), clickhouse)
    return sum(processor.process(batch).events for batch in fixture.batches(project_id))


def ensure_eval_project(clickhouse: ClickHouseClient) -> int:
    """The eval project, created and loaded on first use. Event uuids are stable, so a
    reload after a partial failure doesn't double-count."""
    project, created = Project.objects.get_or_create(name=fixture.EVAL_PROJECT_NAME)
    if created or not project.persondistinctid_set.exists():
        load_fixture(project.pk, clickhouse)
    return project.pk


async def run(
    project_id: int, clickhouse: ClickHouseClient, model: ModelClient, questions: list[Question]
) -> Report:
    server = build_server(lambda: project_id, lambda: clickhouse)
    async with Client(server) as client:
        return await run_suite(client, model, questions)
