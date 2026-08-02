"""Defect: a `service_time_minutes` column polars infers as Boolean crashes.

If every non-null cell in the `service_time_minutes` column looks like a
boolean literal (e.g. "True"), polars infers the column dtype as Boolean.
`validate_csv` then does
`df.with_columns(pl.col("service_time_minutes").fill_null(service_time_default))`
where `service_time_default` is a float — filling a Boolean column with a
float default is an ambiguous cast that polars refuses, raising
`polars.exceptions.InvalidOperationError`. That exception is never caught
anywhere between here and the FastAPI handler, so POST /sessions returns an
unhandled 500 instead of a 422.

Impact: a CSV where a normally-numeric column happens to contain only
boolean-looking strings crashes the request rather than being rejected (or
coerced) cleanly, unlike a garbage string in the same column (see
test_service_time_non_numeric_string_crash.py, a related but distinct
defect — that one is an uncaught builtin ValueError from `float()`, this one
is an uncaught polars InvalidOperationError from `fill_null`).
"""

from __future__ import annotations

from tests.adversary.conftest import upload


def test_boolean_inferred_service_time_column_returns_422_not_500(client) -> None:
    csv_data = b"""route_id,stop_sequence,latitude,longitude,service_time_minutes
R-001,0,32.825,-96.775,
R-001,1,32.830,-96.770,True
"""
    resp = upload(client, csv_data)
    assert resp.status_code != 500, (
        f"a boolean-inferred service_time_minutes column crashed the request "
        f"instead of producing a 422: got {resp.status_code} {resp.text!r}"
    )
    assert resp.status_code == 422
