"""Regression guards promoted from robustness run 1.

Each of these was a real defect the adversarial harness reproduced against
init-main. They are inverted here — asserting the fixed behaviour rather than
the bug — and live in the main suite so CI holds the line permanently.

See ROBUSTNESS.md for the promotion workflow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from routebench.app.api import routes as routes_module
from routebench.app.api.app import create_app
from routebench.core.config import AnalysisConfig, Settings
from routebench.core.validation import validate_csv
from routebench.infra.storage.local import LocalStorageBackend

VALID_CSV = b"""route_id,stop_sequence,latitude,longitude
R-001,0,32.825,-96.775
R-001,1,32.830,-96.770
R-001,2,32.835,-96.765
"""


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        anthropic_api_key="test-key",
        storage_path=str(tmp_path / "sessions"),
        storage_backend="local",
    )
    app = create_app(settings=settings)
    # The per-IP rate limit (10/hour) is keyed on the client address, and every
    # TestClient shares "testserver", so a file with a dozen uploads starts
    # earning 429s that have nothing to do with what it is testing. Rate limiting
    # has its own coverage; here it is noise.
    routes_module.limiter.enabled = False
    return TestClient(app, raise_server_exceptions=False)


def _upload(client: TestClient, data: bytes, config: str | None = None):
    payload = {"config": config} if config is not None else None
    return client.post("/sessions", files={"file": ("r.csv", data, "text/csv")}, data=payload)


class TestNaNCoordinates:
    """`nan < -90` and `nan > 90` are BOTH False, so a NaN sailed through the
    range guard as if it were in range. On a depot row it was accepted silently
    — is_valid=True — and carried into routing, distance maths, and geojson."""

    def test_nan_latitude_on_a_stop_is_rejected(self, tmp_path: Path) -> None:
        csv = tmp_path / "nan_stop.csv"
        csv.write_bytes(VALID_CSV.replace(b"32.830,-96.770", b"NaN,-96.770"))
        fleet, report = validate_csv(csv)
        assert fleet is None
        assert any(e.code == "OUT_OF_RANGE" for e in report.errors)

    def test_nan_latitude_on_the_depot_is_rejected(self, tmp_path: Path) -> None:
        """The depot row is the dangerous one: it has no Stop model behind it,
        so nothing downstream would have caught the NaN either."""
        csv = tmp_path / "nan_depot.csv"
        csv.write_bytes(VALID_CSV.replace(b"32.825,-96.775", b"NaN,-96.775"))
        fleet, report = validate_csv(csv)
        assert fleet is None
        assert not report.is_valid

    def test_nan_longitude_is_rejected(self, tmp_path: Path) -> None:
        csv = tmp_path / "nan_lon.csv"
        csv.write_bytes(VALID_CSV.replace(b"32.830,-96.770", b"32.830,NaN"))
        fleet, _ = validate_csv(csv)
        assert fleet is None

    def test_infinity_is_rejected(self, tmp_path: Path) -> None:
        csv = tmp_path / "inf.csv"
        csv.write_bytes(VALID_CSV.replace(b"32.830,-96.770", b"Infinity,-96.770"))
        fleet, _ = validate_csv(csv)
        assert fleet is None

    def test_nan_upload_earns_a_422_not_a_500(self, tmp_path: Path) -> None:
        response = _upload(_client(tmp_path), VALID_CSV.replace(b"32.830", b"NaN"))
        assert response.status_code == 422

    def test_ordinary_out_of_range_still_rejected(self, tmp_path: Path) -> None:
        """Guard the guard: the NaN fix must not have loosened the real check."""
        csv = tmp_path / "oor.csv"
        csv.write_bytes(VALID_CSV.replace(b"32.830,-96.770", b"91.0,-96.770"))
        fleet, report = validate_csv(csv)
        assert fleet is None
        assert any(e.code == "OUT_OF_RANGE" for e in report.errors)

    def test_valid_coordinates_still_accepted(self, tmp_path: Path) -> None:
        csv = tmp_path / "ok.csv"
        csv.write_bytes(VALID_CSV)
        fleet, _ = validate_csv(csv)
        assert fleet is not None


class TestStorageKeyConfinement:
    """`base / key` gives an absolute key POSIX join semantics: it REPLACES the
    base. "/etc/passwd" resolved to /etc/passwd, discarding the storage root —
    an arbitrary file read/append primitive."""

    def test_absolute_key_is_refused(self, tmp_path: Path) -> None:
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        with pytest.raises(ValueError, match="escapes the storage root"):
            storage._object_path("/etc/passwd")

    def test_traversal_key_is_refused(self, tmp_path: Path) -> None:
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        with pytest.raises(ValueError, match="escapes the storage root"):
            storage._object_path("../../../etc/passwd")

    @pytest.mark.asyncio()
    async def test_append_cannot_escape(self, tmp_path: Path) -> None:
        target = tmp_path / "escaped.txt"
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        with pytest.raises(ValueError):
            await storage.append_object(str(target), b"pwned\n")
        assert not target.exists(), "append escaped the storage root"

    @pytest.mark.asyncio()
    async def test_read_cannot_escape(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("classified")
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        with pytest.raises(ValueError):
            await storage.read_object(str(secret))

    @pytest.mark.asyncio()
    async def test_ordinary_keys_still_work(self, tmp_path: Path) -> None:
        """The real ledger key must be unaffected — confinement, not paranoia."""
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        await storage.append_object("ledger/2026-07-17.jsonl", b'{"cost_usd":1.0}\n')
        assert await storage.read_object("ledger/2026-07-17.jsonl") == b'{"cost_usd":1.0}\n'


class TestConfigReachesTheAnalysis:
    """The config was accepted, persisted, echoed back — and dropped on the
    floor, because neither call site passed it to validate_csv. The constraints
    panel promises that what the user saw is what runs; this is that promise."""

    def test_service_time_default_is_applied(self, tmp_path: Path) -> None:
        csv = tmp_path / "svc.csv"
        csv.write_bytes(VALID_CSV)
        config = AnalysisConfig(service_time={"default_minutes": 17.0})  # type: ignore[arg-type]
        fleet, _ = validate_csv(csv, config)
        assert fleet is not None
        assert all(s.service_time_minutes == 17.0 for s in fleet.routes[0].stops)

    def test_default_config_is_unchanged(self, tmp_path: Path) -> None:
        csv = tmp_path / "svc_default.csv"
        csv.write_bytes(VALID_CSV)
        fleet, _ = validate_csv(csv)
        assert fleet is not None
        assert all(s.service_time_minutes == 5.0 for s in fleet.routes[0].stops)

    def test_the_api_passes_config_through(self, tmp_path: Path) -> None:
        """End to end: a config the API accepts must reach the analysis, and the
        persisted copy must be the one that runs."""
        client = _client(tmp_path)
        response = _upload(
            client, VALID_CSV, json.dumps({"service_time": {"default_minutes": 23.0}})
        )
        assert response.status_code == 202
        session_id = response.json()["session_id"]
        saved = json.loads((tmp_path / "sessions" / session_id / "config.json").read_text())
        assert saved["service_time"]["default_minutes"] == 23.0


class TestConfigShapeAndBounds:
    """Valid-but-wrong-shaped JSON reached AnalysisConfig(**data) and raised
    TypeError, which the handler did not catch — a 500 for a bad request. And
    unbounded config values were rejected later by models that never expected
    user input, producing the same 500."""

    @pytest.mark.parametrize("payload", ["[1,2,3]", '"hello"', "42", "true", "null"])
    def test_non_object_config_earns_a_422(self, tmp_path: Path, payload: str) -> None:
        assert _upload(_client(tmp_path), VALID_CSV, payload).status_code == 422

    def test_malformed_json_earns_a_422(self, tmp_path: Path) -> None:
        assert _upload(_client(tmp_path), VALID_CSV, "{not json").status_code == 422

    @pytest.mark.parametrize(
        "config",
        [
            '{"service_time": {"default_minutes": -1}}',
            '{"work_rules": {"max_shift_hours": -5}}',
            '{"work_rules": {"max_shift_hours": 0}}',
            '{"work_rules": {"lunch_minutes": -30}}',
            '{"work_rules": {"pre_trip_minutes": -15}}',
            '{"sequencing_threshold": -1}',
            '{"underutilization_threshold": 5}',
        ],
    )
    def test_out_of_bounds_config_earns_a_422(self, tmp_path: Path, config: str) -> None:
        assert _upload(_client(tmp_path), VALID_CSV, config).status_code == 422

    def test_a_sane_config_still_works(self, tmp_path: Path) -> None:
        response = _upload(
            _client(tmp_path), VALID_CSV, '{"service_time": {"default_minutes": 12}}'
        )
        assert response.status_code == 202
