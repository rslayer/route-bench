"""Defect: the user-supplied `config.service_time.default_minutes` is
accepted, persisted, and echoed back — but silently has NO effect, because
neither production call site ever passes `config` into `validate_csv`.

routebench/app/api/routes.py:
    fleet, report = validate_csv(tmp_path)          # no config argument

routebench/app/pipeline.py (run_session):
    fleet, report = validate_csv(upload_path)        # no config argument

`validate_csv`'s signature is `validate_csv(path, config=None)`, and when
`config is None` it builds a fresh `AnalysisConfig()` internally — so
`service_time.default_minutes` (and any other validate_csv-consumed field
of a future config) can never be anything but the hardcoded default of
5.0, no matter what the caller uploaded.

This is demonstrated two ways:

1. Through the public API (test below): a CSV missing
   `service_time_minutes` is uploaded together with a config that sets an
   extreme `default_minutes` (-10). If the config were applied, this would
   surface as a crash (see test_config_absurd_values.py, which reproduces
   that crash by calling validate_csv directly WITH the config). Through
   the API it does not — proving the config value never reached
   validate_csv.

2. Directly against the library function, showing that validate_csv DOES
   honor a config argument when one is actually passed — isolating the
   defect to the two call sites that omit it, not to validate_csv itself.
"""

from __future__ import annotations

import pathlib
import tempfile

from fastapi.testclient import TestClient

from routebench.core.config import AnalysisConfig
from routebench.core.validation import validate_csv
from tests.adversary.conftest import upload

CSV_NO_SERVICE_TIME = (
    b"route_id,stop_sequence,latitude,longitude\n"
    b"R-001,0,32.825,-96.775\n"
    b"R-001,1,32.830,-96.770\n"
)


def test_validate_csv_itself_does_honor_a_passed_config() -> None:
    """Sanity check: validate_csv is NOT the problem — it applies config.service_time
    correctly when the caller actually passes it in.
    """
    path = pathlib.Path(tempfile.mktemp(suffix=".csv"))
    path.write_bytes(CSV_NO_SERVICE_TIME)

    custom_config = AnalysisConfig(service_time={"default_minutes": 42.0})
    fleet, report = validate_csv(path, custom_config)

    assert fleet is not None
    assert fleet.routes[0].stops[0].service_time_minutes == 42.0


def test_api_upload_ignores_service_time_config(client: TestClient) -> None:
    """The production request path never plumbs `config` into validate_csv.

    A negative `service_time.default_minutes` would violate Stop's
    `Field(ge=0)` constraint and crash validate_csv if it were actually
    applied (proven directly against validate_csv+config in
    test_config_absurd_values.py). Via the real upload endpoint it instead
    succeeds cleanly with a 202 — which is only possible because the
    config value never reached validate_csv and the hardcoded 5.0 minute
    default was used instead.
    """
    resp = upload(
        client,
        CSV_NO_SERVICE_TIME,
        config='{"service_time": {"default_minutes": -10}}',
    )

    assert resp.status_code == 202, (
        "Expected the upload to succeed (202) because the negative "
        "service_time default never actually reaches validate_csv through "
        f"the API — but got {resp.status_code}: {resp.text!r}. "
        "If this assertion starts failing, it likely means the config "
        "plumbing bug was fixed (config now reaches validate_csv), which "
        "would also mean the negative value now needs to be rejected with "
        "a 422 rather than crashing with a 500 — see "
        "test_config_absurd_values.py for that follow-on defect."
    )

    # Directly reproduce what SHOULD have happened if config were honored:
    # confirm the negative value really would crash validate_csv when
    # actually applied, so the 202 above cannot be explained by the value
    # being harmless.
    path = pathlib.Path(tempfile.mktemp(suffix=".csv"))
    path.write_bytes(CSV_NO_SERVICE_TIME)
    would_be_config = AnalysisConfig(service_time={"default_minutes": -10})
    try:
        validate_csv(path, would_be_config)
        crashed = False
    except Exception:
        crashed = True
    assert crashed, (
        "Expected applying default_minutes=-10 directly to crash validate_csv "
        "(Stop.service_time_minutes requires >= 0); if it no longer crashes, "
        "this test's premise needs revisiting."
    )
