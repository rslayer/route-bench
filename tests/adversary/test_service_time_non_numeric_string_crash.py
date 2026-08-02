"""Defect: a non-numeric `service_time_minutes` cell crashes validate_csv.

Impact: POST /sessions returns an unhandled 500 instead of a 422 for a CSV
that every other bad cell in this same column would get cleanly rejected for
(a negative service time is a clean 422 via Stop's own pydantic validation —
run 2 fixed that). Root cause: `validate_csv` calls
`float(srow.get("service_time_minutes", 5.0) or 5.0)` directly as an argument
to `Stop(...)`, inside the `try/except PydanticValidationError` block — but a
plain string like "abc" raises the *builtin* `ValueError` from `float()`,
which is a different exception class than `PydanticValidationError` and so is
never caught. The sibling optional numeric fields (demand_units/weight/volume)
go through `_safe_float`, which does catch `ValueError`/`TypeError` — this is
the one optional numeric field that skips that helper.
"""

from __future__ import annotations

from tests.adversary.conftest import upload


def test_non_numeric_service_time_returns_422_not_500(client) -> None:
    csv_data = b"""route_id,stop_sequence,latitude,longitude,service_time_minutes
R-001,0,32.825,-96.775,
R-001,1,32.830,-96.770,abc
"""
    resp = upload(client, csv_data)
    assert resp.status_code != 500, (
        f"non-numeric service_time_minutes crashed the request instead of "
        f"producing a 422: got {resp.status_code} {resp.text!r}"
    )
    assert resp.status_code == 422
