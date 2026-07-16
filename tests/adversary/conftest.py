"""Shared fixtures for the robustness-harness adversary tests.

These are pre-provided and VERIFIED working, so the adversary spends its turns
attacking rather than reverse-engineering setup.

Two things matter here:

* ``raise_server_exceptions=False`` — an unhandled 500 surfaces as an ordinary
  response instead of blowing up the test process. That is what a real deployed
  client sees, and a 500 is exactly the kind of defect worth reporting.
* ``StubMatrixProvider`` — the analysis pipeline needs travel times, not OSRM.
  Stubbing the provider makes the whole pipeline reachable with no Docker, no
  network, and no OSRM instance.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from routebench.app.api.app import create_app
from routebench.core.config import Settings
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.storage.local import LocalStorageBackend

# A minimal CSV the validator accepts. Start from this and mutate it.
#
# stop_sequence=0 is the DEPOT row: the validator consumes it into the route's
# depot coordinates, leaving stops 1..n. Omit it and you get a 422
# MISSING_DEPOT, not a valid fleet.
VALID_CSV = b"""route_id,stop_sequence,latitude,longitude
R-001,0,32.825,-96.775
R-001,1,32.830,-96.770
R-001,2,32.835,-96.765
R-001,3,32.840,-96.760
"""


class StubMatrixProvider:
    """Deterministic finite travel times — the pipeline without OSRM.

    Mirrors what the real OSRM provider returns in shape, so anything the
    pipeline does with a real matrix it also does with this one.
    """

    name: str = "stub"

    def __init__(self, duration_s: float = 300.0, distance_m: float = 5000.0) -> None:
        self._duration_s = duration_s
        self._distance_m = distance_m

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        n_o, n_d = len(origins), len(destinations)
        return MatrixResult(
            durations_seconds=[[self._duration_s] * n_d for _ in range(n_o)],
            distances_meters=[[self._distance_m] * n_d for _ in range(n_o)],
            provider="stub",
            cached=False,
        )


@pytest.fixture()
def stub_matrix_provider() -> StubMatrixProvider:
    """A matrix provider that needs no OSRM. Inject where a provider is wanted."""
    return StubMatrixProvider()


@pytest.fixture()
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(base_path=str(tmp_path / "sessions"))


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        storage_path=str(tmp_path / "sessions"),
        anthropic_api_key="test-key",
        osrm_host="http://localhost:5000",
        admin_token="test-admin-token",
        storage_backend="local",
    )


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    """In-process API client. No network, no OSRM, no worker.

    The lifespan is deliberately not started, so uploads are validated
    synchronously and then queue without being processed. That is the right
    shape for probing the request surface: POST /sessions validates the CSV
    *before* enqueuing, so every validation path is reachable here.
    """
    application = create_app(settings=settings)
    return TestClient(application, raise_server_exceptions=False)


def upload(client: TestClient, data: bytes, *, config: str | None = None, name: str = "r.csv"):
    """POST a CSV to /sessions. Returns the response."""
    files = {"file": (name, data, "text/csv")}
    payload = {"config": config} if config is not None else None
    return client.post("/sessions", files=files, data=payload)
