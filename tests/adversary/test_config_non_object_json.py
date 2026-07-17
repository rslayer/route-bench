"""Defect: a syntactically-valid `config` JSON payload that isn't an object
(e.g. a top-level array, string, or number) crashes POST /sessions with an
unhandled 500 instead of a 422.

Root cause (routebench/app/api/routes.py, create_session):

    try:
        config_data = json.loads(config)
        analysis_config = AnalysisConfig(**config_data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc

`json.loads("[1,2,3]")` succeeds (it's valid JSON) and returns a `list`.
`AnalysisConfig(**config_data)` then does `AnalysisConfig(**[1, 2, 3])`,
which raises `TypeError: ... argument after ** must be a mapping, not
list` — a `TypeError`, not a `JSONDecodeError` or `ValueError`. The except
clause does not catch `TypeError`, so it propagates uncaught to the ASGI
layer as a 500.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.adversary.conftest import VALID_CSV, upload


def test_json_array_config_returns_422_not_500(client: TestClient) -> None:
    resp = upload(client, VALID_CSV, config="[1, 2, 3]")

    assert resp.status_code != 500, (
        "A non-object (array) config payload crashed the request handler with "
        f"a raw 500 instead of a 422 validation error; got body: {resp.text!r}"
    )
    assert resp.status_code == 422


def test_json_string_config_returns_422_not_500(client: TestClient) -> None:
    resp = upload(client, VALID_CSV, config='"just a string"')

    assert resp.status_code != 500, (
        "A non-object (string) config payload crashed the request handler with "
        f"a raw 500 instead of a 422 validation error; got body: {resp.text!r}"
    )
    assert resp.status_code == 422


def test_json_number_config_returns_422_not_500(client: TestClient) -> None:
    resp = upload(client, VALID_CSV, config="42")

    assert resp.status_code != 500, (
        "A non-object (number) config payload crashed the request handler with "
        f"a raw 500 instead of a 422 validation error; got body: {resp.text!r}"
    )
    assert resp.status_code == 422
