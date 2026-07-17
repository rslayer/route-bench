"""Defect: a negative `service_time_minutes` on a non-depot row crashes
`validate_csv` with an unhandled `pydantic.ValidationError` instead of being
turned into a `ValidationError`/422.

Impact: `POST /sessions` calls `validate_csv` synchronously in the request
handler (see `routes.py::create_session`) with no try/except around it, so
this reaches the public API as an unhandled 500 Internal Server Error for a
plain CSV upload — no config, no auth, no special headers.

Root cause: `validate_csv` in `core/validation.py` only range-checks
`latitude`/`longitude`/nulls itself; `service_time_minutes` is left to the
`Stop(...)` pydantic model constructor (which has `ge=0`) to catch, and
that constructor call is not wrapped in a try/except the way lat/lon casts
are. Every other numeric field that has a pydantic-level bound
(`Stop.demand_units`, etc.) has the same unguarded construction and is
presumably equally exposed; service_time_minutes is the simplest
reproduction since it needs only a plain negative number, no other
constraint.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from routebench.core.validation import validate_csv

NEGATIVE_SERVICE_TIME_CSV = b"""route_id,stop_sequence,latitude,longitude,service_time_minutes
R-001,0,32.825,-96.775,5
R-001,1,32.830,-96.770,-100
"""


def test_negative_service_time_does_not_crash_the_validator(tmp_path: Path) -> None:
    """A bad but well-typed CSV value must produce a ValidationError, not raise."""
    csv_path = tmp_path / "negative_service_time.csv"
    csv_path.write_bytes(NEGATIVE_SERVICE_TIME_CSV)

    # This is expected to return (None, report_with_errors) like every other
    # rejected upload. Instead it raises pydantic_core.ValidationError, which
    # propagates straight out of validate_csv.
    fleet, report = validate_csv(csv_path)

    assert fleet is None
    assert any(e.column == "service_time_minutes" for e in report.errors)


def test_negative_service_time_upload_earns_a_422_not_a_500(client: TestClient) -> None:
    """End to end: the public API must never 500 on a malformed upload."""
    response = client.post(
        "/sessions",
        files={"file": ("r.csv", NEGATIVE_SERVICE_TIME_CSV, "text/csv")},
    )
    assert response.status_code == 422, (
        f"expected 422 for invalid service_time_minutes, got {response.status_code}: "
        f"{response.text[:300]}"
    )
