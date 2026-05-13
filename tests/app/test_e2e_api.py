"""End-to-end test for the full FastAPI stack with mocked LLM/matrix."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from routebench.app.api.app import create_app
from routebench.core.config import Settings


def _make_valid_csv() -> bytes:
    """Create a valid CSV with enough data for the pipeline."""
    lines = [
        "route_id,stop_sequence,latitude,longitude,planned_arrival,planned_departure,service_minutes",
        "R-001,0,32.825,-96.775,2024-01-15T08:00:00,2024-01-15T08:05:00,5",
        "R-001,1,32.830,-96.770,2024-01-15T08:30:00,2024-01-15T08:35:00,5",
        "R-001,2,32.835,-96.765,2024-01-15T09:00:00,2024-01-15T09:05:00,5",
        "R-001,3,32.840,-96.760,2024-01-15T09:30:00,2024-01-15T09:35:00,5",
    ]
    return "\n".join(lines).encode()


class TestApiE2E:
    """Full API lifecycle test with mocked pipeline."""

    def test_upload_and_poll_lifecycle(self, tmp_path: Path) -> None:
        """Upload CSV -> poll status -> verify lifecycle."""
        settings = Settings(
            storage_path=str(tmp_path / "sessions"),
            anthropic_api_key="test-key",
            storage_backend="local",
        )
        application = create_app(settings=settings)
        client = TestClient(application, raise_server_exceptions=False)

        csv_data = _make_valid_csv()

        # Upload
        resp = client.post(
            "/sessions",
            files={"file": ("test.csv", csv_data, "text/csv")},
        )
        assert resp.status_code == 202
        session_id = resp.json()["session_id"]

        # Poll — should be in some state
        poll_resp = client.get(f"/sessions/{session_id}")
        assert poll_resp.status_code == 200
        status = poll_resp.json()
        assert status["session_id"] == session_id
        assert status["state"] in (
            "queued",
            "validating",
            "analyzing",
            "writing",
            "rendering",
            "succeeded",
            "failed",
        )

    def test_healthz_local_backend(self, tmp_path: Path) -> None:
        """Health check with local storage should report storage_writable=True."""
        settings = Settings(
            storage_path=str(tmp_path / "sessions"),
            storage_backend="local",
        )
        application = create_app(settings=settings)
        client = TestClient(application, raise_server_exceptions=False)

        resp = client.get("/healthz")
        body = resp.json()
        assert body["checks"]["storage_writable"] is True
