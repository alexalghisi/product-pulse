from __future__ import annotations

import gzip
import json

import pytest

from product_pulse.ingestion.envelope import MAX_BODY_BYTES, EnvelopeError, decode


def test_batch_with_token_in_body() -> None:
    env = decode(
        json.dumps({"api_key": "pp_x", "batch": [{"event": "a"}, {"event": "b"}]}).encode()
    )
    assert env.token == "pp_x"
    assert len(env.events) == 2


def test_single_event_and_bare_list_with_bearer_token() -> None:
    assert decode(json.dumps({"api_key": "t", "event": "a"}).encode()).events == [
        {"api_key": "t", "event": "a"}
    ]
    env = decode(b'[{"event": "a"}]', header_token="from-header")
    assert env.token == "from-header"


def test_gzip_by_header_query_flag_or_magic_bytes() -> None:
    body = gzip.compress(json.dumps({"api_key": "t", "batch": [{"event": "a"}]}).encode())
    for kwargs in ({"content_encoding": "gzip"}, {"query_compression": "gzip-js"}, {}):
        assert decode(body, **kwargs).token == "t"


def test_gzip_bomb_is_refused_without_inflating_it() -> None:
    bomb = gzip.compress(b"[" + b" " * (MAX_BODY_BYTES + 10) + b"]")
    assert len(bomb) < 100_000
    with pytest.raises(EnvelopeError) as info:
        decode(bomb)
    assert info.value.status == 413


@pytest.mark.parametrize(
    "body", [b"not json", b'"a string"', b'{"batch": "nope"}', b"\x1f\x8bgarbage"]
)
def test_malformed_bodies_are_400(body: bytes) -> None:
    with pytest.raises(EnvelopeError) as info:
        decode(body)
    assert info.value.status == 400
