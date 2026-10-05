from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from product_pulse.ingestion.events import CapturedEvent

T0 = datetime(2026, 6, 1, tzinfo=UTC)


def make_event(
    distinct_id: str,
    event: str = "$pageview",
    *,
    project_id: int = 1,
    at: datetime | None = None,
    minutes: float = 0,
    properties: dict[str, Any] | None = None,
    uuid: str | None = None,
) -> CapturedEvent:
    return CapturedEvent(
        uuid=uuid or str(uuid4()),
        project_id=project_id,
        event=event,
        distinct_id=distinct_id,
        properties=properties or {},
        timestamp=(at or T0) + timedelta(minutes=minutes),
    )


def identify(
    user_id: str, anon_id: str, *, project_id: int = 1, minutes: float = 0
) -> CapturedEvent:
    return make_event(
        user_id,
        "$identify",
        project_id=project_id,
        minutes=minutes,
        properties={"$anon_distinct_id": anon_id},
    )
