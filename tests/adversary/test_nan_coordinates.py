"""Defect: NaN latitude/longitude bypasses validate_csv's range check.

Impact:
  * A NaN coordinate on a non-depot stop row causes an UNHANDLED 500 at the
    API (POST /sessions), instead of the expected 422 with a clear
    OUT_OF_RANGE error.
  * A NaN coordinate on the DEPOT row (stop_sequence=0) is accepted
    completely silently: no error, no warning, `is_valid=True`, and the
    resulting Fleet carries a NaN depot_lat/depot_lon into the rest of the
    pipeline (routing, distance math, geojson export, ...).

Root cause: `validate_csv`'s per-row OUT_OF_RANGE check
(routebench/core/validation.py) uses plain Python comparisons:

    if lat is not None and (lat < -90 or lat > 90): ...

`float('nan') < -90` and `float('nan') > 90` are BOTH `False`, so a NaN
value sails through this guard as if it were in range — unlike pydantic's
`Field(ge=-90, le=90)` on `Stop`, which correctly rejects NaN.

That asymmetry produces two different failure modes depending on whether
the NaN lands on the depot row (no Stop model applied to depot coordinates
in `Route`, so it is silently accepted) or a regular stop row (the `Stop`
model's Field constraints DO catch it, but nothing in validate_csv wraps
that construction in a try/except, so the pydantic ValidationError
propagates uncaught all the way to the ASGI layer as a 500).
"""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from tests.adversary.conftest import upload


def test_nan_on_non_depot_stop_causes_unhandled_500(client: TestClient) -> None:
    """A NaN coordinate on a delivery stop should be rejected with a 422,
    not crash the request handler.
    """
    csv = (
        b"route_id,stop_sequence,latitude,longitude\n"
        b"R-001,0,32.825,-96.775\n"
        b"R-001,1,nan,-96.770\n"
        b"R-001,2,32.835,-96.765\n"
    )

    resp = upload(client, csv)

    assert resp.status_code != 500, (
        "NaN latitude on a non-depot stop crashed the request handler "
        f"with a raw 500 instead of a 422 validation error; got body: {resp.text!r}"
    )
    assert resp.status_code == 422


def test_nan_on_depot_row_is_silently_accepted() -> None:
    """A NaN depot coordinate should be rejected (or at least flagged), not
    silently pass through into a 'valid' Fleet.
    """
    import pathlib
    import tempfile

    from routebench.core.validation import validate_csv

    csv = (
        b"route_id,stop_sequence,latitude,longitude\n"
        b"R-001,0,nan,-96.775\n"
        b"R-001,1,32.830,-96.770\n"
        b"R-001,2,32.835,-96.765\n"
    )
    path = pathlib.Path(tempfile.mktemp(suffix=".csv"))
    path.write_bytes(csv)

    fleet, report = validate_csv(path)

    assert not (fleet is not None and math.isnan(fleet.routes[0].depot_lat)), (
        "NaN depot latitude was accepted as a valid Fleet "
        f"(is_valid={report.is_valid}, depot_lat={fleet.routes[0].depot_lat if fleet else 'n/a'}); "
        "expected an OUT_OF_RANGE validation error."
    )
