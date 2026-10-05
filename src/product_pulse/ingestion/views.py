"""POST /capture: authenticate the project token, validate, buffer, answer.

The request never touches Postgres on the hot path (tokens are cached) and never waits
for ClickHouse: accepted events are in Redis when the response is sent.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import redis
from django.conf import settings
from rest_framework.parsers import BaseParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from product_pulse.core.models import Project
from product_pulse.ingestion.buffer import BufferFull, EventSink, RedisStreamBuffer
from product_pulse.ingestion.envelope import EnvelopeError, decode
from product_pulse.ingestion.events import validate_batch


class RawBodyParser(BaseParser):
    """Accept any content type; the body is decoded by `envelope.decode`."""

    media_type = "*/*"

    def parse(self, stream: Any, media_type: Any = None, parser_context: Any = None) -> Any:
        return stream.read()


class TokenCache:
    """Project tokens rarely change; a short TTL keeps revocation reasonably quick."""

    def __init__(self, ttl_s: float = 60.0) -> None:
        self.ttl_s = ttl_s
        self._entries: dict[str, tuple[int | None, float]] = {}
        self._lock = threading.Lock()

    def project_id(self, token: str) -> int | None:
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(token)
        if hit and now - hit[1] < self.ttl_s:
            return hit[0]
        project_id = Project.objects.filter(api_token=token).values_list("id", flat=True).first()
        with self._lock:
            if len(self._entries) > 10_000:
                self._entries.clear()
            self._entries[token] = (project_id, now)
        return project_id

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


token_cache = TokenCache()


@lru_cache(maxsize=1)
def default_sink() -> RedisStreamBuffer:
    return RedisStreamBuffer(
        redis.Redis.from_url(settings.PULSE_REDIS_URL),
        stream=settings.PULSE_STREAM,
        high_watermark=settings.PULSE_STREAM_HIGH_WATERMARK,
    )


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


class CaptureView(APIView):
    authentication_classes = ()
    permission_classes = ()
    parser_classes = (RawBodyParser,)
    # Overridable in tests; production uses the Redis stream.
    sink_factory = staticmethod(default_sink)

    def post(self, request: Request) -> Response:
        body = request.data if isinstance(request.data, bytes) else b""
        try:
            envelope = decode(
                body,
                content_encoding=request.headers.get("Content-Encoding"),
                query_compression=request.query_params.get("compression"),
                header_token=_bearer(request),
            )
        except EnvelopeError as exc:
            return Response({"error": str(exc)}, status=exc.status)

        if not envelope.token:
            return Response({"error": "missing project token (api_key)"}, status=401)
        project_id = token_cache.project_id(envelope.token)
        if project_id is None:
            return Response({"error": "unknown project token"}, status=401)

        accepted, rejected = validate_batch(
            envelope.events, project_id=project_id, now=datetime.now(UTC)
        )
        sink: EventSink = self.sink_factory()
        try:
            sink.publish(accepted)
        except BufferFull:
            return Response(
                {"error": "ingestion is behind, retry later"},
                status=503,
                headers={"Retry-After": "5"},
            )
        return Response(
            {
                "status": "ok",
                "accepted": len(accepted),
                "rejected": [{"index": r.index, "error": r.error} for r in rejected],
            }
        )
