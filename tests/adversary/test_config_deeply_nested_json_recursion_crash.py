"""Defect: a deeply nested `config` JSON payload crashes the request handler.

`POST /sessions` parses the `config` form field with `json.loads(config)`
inside a `try/except json.JSONDecodeError`. Python's `json` module is a
recursive-descent parser, so a sufficiently deeply nested structure (here,
10,000+ levels of `[[[...]]]`) raises the builtin `RecursionError` instead of
`JSONDecodeError` once it exceeds the interpreter's recursion limit.
`RecursionError` is not caught by the existing handler, so it propagates
uncaught and FastAPI returns an unhandled 500.

Impact: a request body of a few tens of KB — well under the 50MB upload cap,
and trivially cheap to generate — crashes the request instead of getting a
422 "invalid config" like every other malformed config payload. This is a
cheap, repeatable DoS-adjacent crash vector on the ingest endpoint.
"""

from __future__ import annotations

from tests.adversary.conftest import VALID_CSV, upload


def _deeply_nested_json_array(depth: int) -> str:
    return "[" * depth + "1" + "]" * depth


def test_deeply_nested_config_json_returns_422_not_500(client) -> None:
    nested_config = _deeply_nested_json_array(10_000)
    resp = upload(client, VALID_CSV, config=nested_config)
    assert resp.status_code != 500, (
        f"deeply nested config JSON crashed the request (RecursionError) "
        f"instead of producing a 422: got {resp.status_code} {resp.text!r}"
    )
    assert resp.status_code == 422
