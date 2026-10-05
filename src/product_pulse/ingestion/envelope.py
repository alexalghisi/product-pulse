"""Decoding of /capture request bodies: optional gzip, JSON, single event or batch."""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass

MAX_BODY_BYTES = 20 * 1024 * 1024
MAX_BATCH_EVENTS = 10_000


class EnvelopeError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Envelope:
    token: str | None
    events: list[object]


def gunzip_limited(data: bytes, limit: int = MAX_BODY_BYTES) -> bytes:
    """Decompress gzip without trusting the compressed size: a 20 KB bomb can expand to GBs."""
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        out = decompressor.decompress(data, limit + 1)
    except zlib.error as exc:
        raise EnvelopeError(f"invalid gzip body: {exc}") from exc
    if len(out) > limit or decompressor.unconsumed_tail:
        raise EnvelopeError("decompressed body too large", status=413)
    return out


def is_gzip(content_encoding: str | None, query_compression: str | None, body: bytes) -> bool:
    if content_encoding and "gzip" in content_encoding.lower():
        return True
    if query_compression in {"gzip", "gzip-js"}:
        return True
    return body[:2] == b"\x1f\x8b"


def decode(
    body: bytes,
    *,
    content_encoding: str | None = None,
    query_compression: str | None = None,
    header_token: str | None = None,
) -> Envelope:
    if len(body) > MAX_BODY_BYTES:
        raise EnvelopeError("body too large", status=413)
    if is_gzip(content_encoding, query_compression, body):
        body = gunzip_limited(body)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeError(f"body is not valid JSON: {exc}") from exc

    if isinstance(payload, list):
        token, events = header_token, payload
    elif isinstance(payload, dict):
        body_token = payload.get("api_key") or payload.get("token")
        token = body_token if isinstance(body_token, str) else header_token
        batch = payload.get("batch")
        if batch is None:
            events = [payload]
        elif isinstance(batch, list):
            events = batch
        else:
            raise EnvelopeError("batch must be a list of events")
    else:
        raise EnvelopeError("body must be an event, a batch object or a list of events")

    if len(events) > MAX_BATCH_EVENTS:
        raise EnvelopeError(f"batch larger than {MAX_BATCH_EVENTS} events", status=413)
    return Envelope(token=token, events=list(events))
