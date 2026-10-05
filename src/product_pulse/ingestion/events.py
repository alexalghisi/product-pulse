"""Validation of captured events.

Validation is per event: one bad event in a batch of 500 is reported back with its
index and the other 499 are accepted. SDKs retry whole batches on 5xx, so rejecting
everything for one malformed event would turn a client bug into data loss.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_EVENT_NAME = 200
MAX_DISTINCT_ID = 200
MAX_PROPERTIES_BYTES = 64 * 1024
MAX_FUTURE_SKEW = timedelta(hours=1)

# Values SDKs send when the app passed an unset variable. Accepting them would merge
# every such user into one person.
ILLEGAL_DISTINCT_IDS = frozenset(
    {"", "null", "undefined", "none", "nil", "0", "anonymous", "guest", "[object object]"}
)


@dataclass(frozen=True, slots=True)
class CapturedEvent:
    uuid: str
    project_id: int
    event: str
    distinct_id: str
    properties: dict[str, Any]
    timestamp: datetime

    def to_buffer(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "project_id": self.project_id,
            "event": self.event,
            "distinct_id": self.distinct_id,
            "properties": self.properties,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_buffer(cls, data: dict[str, Any]) -> CapturedEvent:
        return cls(
            uuid=str(data["uuid"]),
            project_id=int(data["project_id"]),
            event=str(data["event"]),
            distinct_id=str(data["distinct_id"]),
            properties=dict(data["properties"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )

    def to_clickhouse(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "project_id": self.project_id,
            "event": self.event,
            "distinct_id": self.distinct_id,
            "properties": json.dumps(self.properties, separators=(",", ":"), default=str),
            "timestamp": self.timestamp.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        }


@dataclass(frozen=True, slots=True)
class Rejection:
    index: int
    error: str


class _RawEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str = Field(min_length=1, max_length=MAX_EVENT_NAME)
    distinct_id: str | int | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None
    uuid: UUID | None = None

    @field_validator("event")
    @classmethod
    def _event_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event name is blank")
        return value


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "event"
    return f"{location}: {error['msg']}"


def validate_event(raw: object, *, project_id: int, now: datetime) -> CapturedEvent | str:
    """Return a CapturedEvent, or the reason the event was rejected."""
    if not isinstance(raw, dict):
        return "event must be a JSON object"
    try:
        parsed = _RawEvent.model_validate(raw)
    except ValidationError as exc:
        return _first_error(exc)

    distinct_id: object = parsed.distinct_id
    if distinct_id is None:
        distinct_id = parsed.properties.get("distinct_id")
    if isinstance(distinct_id, int) and not isinstance(distinct_id, bool):
        distinct_id = str(distinct_id)
    if not isinstance(distinct_id, str):
        return "distinct_id: required (top level or in properties)"
    distinct_id = distinct_id.strip()
    if distinct_id.lower() in ILLEGAL_DISTINCT_IDS:
        return f"distinct_id: {distinct_id!r} is not allowed, it would merge unrelated users"
    if len(distinct_id) > MAX_DISTINCT_ID:
        return f"distinct_id: longer than {MAX_DISTINCT_ID} characters"

    properties = {k: v for k, v in parsed.properties.items() if k != "distinct_id"}
    if len(json.dumps(properties, default=str)) > MAX_PROPERTIES_BYTES:
        return f"properties: larger than {MAX_PROPERTIES_BYTES} bytes"

    timestamp = parsed.timestamp or now
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    timestamp = timestamp.astimezone(UTC)
    if timestamp - now > MAX_FUTURE_SKEW:
        return "timestamp: more than an hour in the future"

    return CapturedEvent(
        uuid=str(parsed.uuid or uuid4()),
        project_id=project_id,
        event=parsed.event,
        distinct_id=distinct_id,
        properties=properties,
        timestamp=timestamp,
    )


def validate_batch(
    raw_events: list[object], *, project_id: int, now: datetime
) -> tuple[list[CapturedEvent], list[Rejection]]:
    accepted: list[CapturedEvent] = []
    rejected: list[Rejection] = []
    for index, raw in enumerate(raw_events):
        result = validate_event(raw, project_id=project_id, now=now)
        if isinstance(result, str):
            rejected.append(Rejection(index=index, error=result))
        else:
            accepted.append(result)
    return accepted, rejected
