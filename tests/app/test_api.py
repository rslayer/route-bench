"""Tests for the FastAPI endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from routebench.app.api.app import create_app
from routebench.core.config import Settings
from routebench.infra.storage.local import LocalStorageBackend


def _make_valid_csv() -> bytes:
    """Create a minimal valid CSV for testing."""
    lines = [
        "route_id,stop_sequence,latitude,longitude",
        "R-001,0,32.825,-96.775",
        "R-001,1,32.830,-96.770",
        "R-001,2,32.835,-96.765",
        "R-001,3,32.840,-96.760",
    ]
    return "\n".join(lines).encode()


def _patch_osrm_probe(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    """Make healthz's OSRM probe return `response` without a network call."""

    async def _get(*args: object, **kwargs: object) -> object:
        return response

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)


@pytest.fixture()
def tmp_storage(tmp_path: Path) -> LocalStorageBackend:
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
def app(settings: Settings) -> TestClient:
    application = create_app(settings=settings)
    return TestClient(application, raise_server_exceptions=False)


class TestSessionUpload:
    """Tests for POST /sessions."""

    def test_upload_valid_csv(self, app: TestClient) -> None:
        """Happy path: upload a valid CSV, get 202 with session_id."""
        csv_data = _make_valid_csv()
        resp = app.post(
            "/sessions",
            files={"file": ("test.csv", csv_data, "text/csv")},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "session_id" in body
        assert "status_url" in body

    def test_upload_empty_file(self, app: TestClient) -> None:
        """Empty file upload should return 422."""
        resp = app.post(
            "/sessions",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert resp.status_code == 422

    def test_upload_malformed_csv(self, app: TestClient) -> None:
        """CSV missing required columns should return 422."""
        csv_data = b"col1,col2\n1,2\n3,4\n"
        resp = app.post(
            "/sessions",
            files={"file": ("bad.csv", csv_data, "text/csv")},
        )
        assert resp.status_code == 422

    def test_upload_with_config(self, app: TestClient) -> None:
        """Upload with config override."""
        csv_data = _make_valid_csv()
        config = json.dumps({"include_benchmark": False, "sequencing_threshold": 1.5})
        resp = app.post(
            "/sessions",
            files={"file": ("test.csv", csv_data, "text/csv")},
            data={"config": config},
        )
        assert resp.status_code == 202

    def test_upload_invalid_config(self, app: TestClient) -> None:
        """Invalid config JSON should return 422."""
        csv_data = _make_valid_csv()
        resp = app.post(
            "/sessions",
            files={"file": ("test.csv", csv_data, "text/csv")},
            data={"config": "not-json"},
        )
        assert resp.status_code == 422


class TestSessionPolling:
    """Tests for GET /sessions/{id}."""

    def test_get_nonexistent_session(self, app: TestClient) -> None:
        """Non-existent session should return 404."""
        resp = app.get("/sessions/nonexistent123")
        assert resp.status_code == 404

    def test_poll_created_session(self, app: TestClient) -> None:
        """Should be able to poll a created session."""
        csv_data = _make_valid_csv()
        create_resp = app.post(
            "/sessions",
            files={"file": ("test.csv", csv_data, "text/csv")},
        )
        session_id = create_resp.json()["session_id"]

        poll_resp = app.get(f"/sessions/{session_id}")
        assert poll_resp.status_code == 200
        body = poll_resp.json()
        assert body["session_id"] == session_id
        valid_states = (
            "queued",
            "validating",
            "analyzing",
            "writing",
            "rendering",
            "succeeded",
            "failed",
        )
        assert body["state"] in valid_states


class TestQueueFull:
    """Tests for 429 when queue is full."""

    def test_queue_full_returns_429(self, settings: Settings) -> None:
        """When queue is full, should return 429."""
        settings.max_queue_depth = 1
        application = create_app(settings=settings)
        client = TestClient(application, raise_server_exceptions=False)

        csv_data = _make_valid_csv()
        # Fill the queue
        resp1 = client.post(
            "/sessions",
            files={"file": ("test1.csv", csv_data, "text/csv")},
        )
        assert resp1.status_code == 202

        # Second should get 429 (queue depth 1)
        resp2 = client.post(
            "/sessions",
            files={"file": ("test2.csv", csv_data, "text/csv")},
        )
        # Could be 202 or 429 depending on timing
        assert resp2.status_code in (202, 429)


class TestBudgetGating:
    """A spent daily budget degrades the run; it does not reject the upload."""

    def test_budget_exceeded_still_accepts_the_upload(self, settings: Settings) -> None:
        """This used to assert 503, and the behaviour it pinned was wrong.

        Rejecting the upload took the whole service offline for the rest of the
        UTC day over a cap on the one part of the analysis that is optional: the
        LLM writes the narrative and picks which analyzers to run, while the
        metrics, findings, benchmark and grade are computed deterministically
        and cost nothing. The budget now withholds the LLM instead — the
        pipeline sees it and takes the deterministic path.
        """
        settings.daily_budget_usd = 0.0  # Force exceeded
        application = create_app(settings=settings)
        application.state.budget_tracker.record_spend(1.0)
        client = TestClient(application, raise_server_exceptions=False)

        csv_data = _make_valid_csv()
        resp = client.post(
            "/sessions",
            files={"file": ("test.csv", csv_data, "text/csv")},
        )
        assert resp.status_code == 202, (
            "a spent budget must not take the service down — the evaluation "
            "still runs, without the LLM"
        )


class TestReportDownload:
    """Tests for report download endpoints."""

    def test_missing_report_returns_404(self, app: TestClient) -> None:
        """Missing report should return 404."""
        resp = app.get("/sessions/nonexistent/report.html")
        assert resp.status_code == 404

    def test_missing_pdf_returns_404(self, app: TestClient) -> None:
        """Missing PDF should return 404."""
        resp = app.get("/sessions/nonexistent/report.pdf")
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "artifact",
        ["report.html", "report.pdf", "analysis.json", "routes.geojson"],
    )
    def test_expired_session_refuses_every_artifact(
        self, app: TestClient, settings: Settings, artifact: str
    ) -> None:
        """An expired session serves nothing, even if the bytes are still there.

        Retention deletes the artifacts, so in practice these 404 anyway. This
        pins the guard independently of that, because the two used to be the
        same check: every route asked only `exists()`, so for as long as a file
        survived on disk an expired session kept serving it. Writing the file
        back after expiry reproduces exactly that state.
        """
        storage = LocalStorageBackend(base_path=settings.storage_path)
        old = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        asyncio.run(
            storage.write(
                "expired-session",
                "status.json",
                json.dumps(
                    {
                        "session_id": "expired-session",
                        "state": "expired",
                        "created_at": old,
                        "updated_at": old,
                    }
                ).encode(),
            )
        )
        asyncio.run(storage.write("expired-session", artifact, b"leftover bytes"))

        resp = app.get(f"/sessions/expired-session/{artifact}")
        assert resp.status_code == 410
        assert b"leftover bytes" not in resp.content
        assert "expired" in resp.json()["detail"].lower()

    def test_live_session_still_serves_artifacts(self, app: TestClient, settings: Settings) -> None:
        """The guard must not break the ordinary case."""
        storage = LocalStorageBackend(base_path=settings.storage_path)
        now = datetime.now(UTC).isoformat()
        asyncio.run(
            storage.write(
                "live-session",
                "status.json",
                json.dumps(
                    {
                        "session_id": "live-session",
                        "state": "succeeded",
                        "created_at": now,
                        "updated_at": now,
                    }
                ).encode(),
            )
        )
        asyncio.run(storage.write("live-session", "report.html", b"<html>hi</html>"))

        resp = app.get("/sessions/live-session/report.html")
        assert resp.status_code == 200
        assert b"<html>hi</html>" in resp.content


class _FakeRemoteStorage:
    """A non-local backend that hands out pre-signed URLs on a different origin.

    Stands in for S3/R2 so tests can assert which artifacts are streamed through
    the API and which are redirected to storage — without a real bucket.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bytes] = {}

    async def write(self, session_id: str, filename: str, data: bytes) -> None:
        self._data[(session_id, filename)] = data

    async def read(self, session_id: str, filename: str) -> bytes:
        try:
            return self._data[(session_id, filename)]
        except KeyError as exc:  # match the real backend's contract
            raise FileNotFoundError(filename) from exc

    async def exists(self, session_id: str, filename: str) -> bool:
        return (session_id, filename) in self._data

    async def presigned_url(self, session_id: str, filename: str, ttl_seconds: int = 900) -> str:
        return f"https://bucket.r2.example.com/{session_id}/{filename}?sig=abc"

    async def is_writable(self) -> bool:
        return True

    async def list_sessions(self) -> list[str]:
        return []


class TestArtifactCorsServing:
    """Browser-fetched artifacts must stream same-origin, never 302 to storage.

    A pre-signed R2 URL is a different origin that returns no CORS headers, so a
    cross-origin `fetch` of it is blocked in the browser ("Failed to fetch")
    even though curl sees the bytes. analysis.json and routes.geojson are fetched
    by the UI with JS, so they must be streamed; reports are opened by
    navigation, so they keep the (cheaper) redirect.
    """

    def _client(self, settings: Settings, storage: _FakeRemoteStorage) -> TestClient:
        application = create_app(settings=settings)
        application.state.storage = storage
        application.state.registry._storage = storage
        return TestClient(application, raise_server_exceptions=False)

    @pytest.mark.parametrize(
        ("artifact", "body"),
        [
            ("analysis.json", b'{"grade": {"overall": {"letter": "B"}}}'),
            ("routes.geojson", b'{"type": "FeatureCollection", "features": []}'),
        ],
    )
    def test_json_artifacts_stream_not_redirect(
        self, settings: Settings, artifact: str, body: bytes
    ) -> None:
        storage = _FakeRemoteStorage()
        asyncio.run(storage.write("s1", artifact, body))
        client = self._client(settings, storage)

        resp = client.get(f"/sessions/s1/{artifact}", follow_redirects=False)

        assert resp.status_code == 200  # streamed, not a 302 to R2
        assert resp.content == body

    def test_reports_still_redirect_to_storage(self, settings: Settings) -> None:
        storage = _FakeRemoteStorage()
        asyncio.run(storage.write("s1", "report.html", b"<html>hi</html>"))
        client = self._client(settings, storage)

        resp = client.get("/sessions/s1/report.html", follow_redirects=False)

        assert resp.status_code == 302
        assert "bucket.r2.example.com" in resp.headers["location"]


class TestHealthz:
    """Tests for GET /healthz."""

    def test_healthz_returns_status(self, app: TestClient) -> None:
        """Health endpoint should return status."""
        resp = app.get("/healthz")
        body = resp.json()
        assert "status" in body
        assert "checks" in body
        assert "storage_writable" in body["checks"]

    def test_google_engine_reports_google_without_probing_osrm(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On the Google engine there is no OSRM to probe, and a live Google
        call would bill money on every health check — so readiness is judged on
        configuration. It reports matrix_mode=google and never touches OSRM,
        even if OSRM_HOST points at nothing."""
        settings.matrix_engine = "google"
        settings.google_maps_api_key = "a-key"
        client = TestClient(create_app(settings=settings), raise_server_exceptions=False)

        # Make any OSRM probe explode; the google path must not call it.
        def _boom(*args: object, **kwargs: object) -> object:
            raise AssertionError("healthz probed OSRM while on the Google engine")

        monkeypatch.setattr(httpx.AsyncClient, "get", _boom)

        resp = client.get("/healthz")
        body = resp.json()
        assert resp.status_code == 200
        assert body["matrix_engine"] == "google"
        assert body["matrix_mode"] == "google"
        assert body["grade_available"] is True
        assert "osrm_reachable" not in body["checks"]

    def test_osrm_error_response_still_counts_as_reachable(
        self, app: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OSRM that answers with an error is up, and must read as up.

        OSRM returns HTTP 400 for several conditions that say nothing about
        health — the probe coordinate being unroutable, or its URL grammar
        rejecting the request outright. Judging on `status_code == 200` turned
        those into "degraded", which 503s the readiness probe and takes a
        healthy deployment out of rotation.
        """

        class _ErrorResponse:
            status_code = 400

            @staticmethod
            def json() -> dict[str, str]:
                return {"code": "InvalidUrl", "message": "URL string malformed"}

        _patch_osrm_probe(monkeypatch, _ErrorResponse())

        body = app.get("/healthz").json()
        assert body["checks"]["osrm_reachable"] is True
        assert body["matrix_mode"] == "osrm"
        assert body["grade_available"] is True

    def test_unreachable_osrm_is_degraded_but_still_serving(
        self, app: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSRM down is a degraded-but-serving state, NOT a 503.

        The status code gates load-balancer routing. Returning 503 here would
        pull a working API — one that still serves haversine estimates with the
        grade withheld — out of rotation on every OSRM blip, defeating the whole
        graceful-degradation design. So OSRM down is 200 with a degraded body;
        only storage being unreachable is a hard 503 (see the next test).
        """

        def _boom(*args: object, **kwargs: object) -> object:
            raise OSError("connection refused")

        monkeypatch.setattr(httpx.AsyncClient, "get", _boom)

        resp = app.get("/healthz")
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == "degraded"
        assert body["checks"]["osrm_reachable"] is False
        assert body["matrix_mode"] == "haversine_estimates"
        assert body["grade_available"] is False

    def test_unwritable_storage_is_a_hard_503(
        self, app: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With nowhere to write a session the service cannot function, so this
        is the one condition that pulls it from rotation."""
        import routebench.infra.storage.local as local_mod

        async def _not_writable(self: object) -> bool:
            return False

        class _OsrmOk:
            status_code = 200

            @staticmethod
            def json() -> dict[str, str]:
                return {"code": "Ok"}

        monkeypatch.setattr(local_mod.LocalStorageBackend, "is_writable", _not_writable)
        _patch_osrm_probe(monkeypatch, _OsrmOk())  # OSRM fine; only storage is down

        resp = app.get("/healthz")
        assert resp.status_code == 503
        assert resp.json()["checks"]["storage_writable"] is False

    def test_non_json_body_is_not_reachable(
        self, app: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A proxy's HTML error page in front of a cold container is not OSRM."""

        class _HtmlResponse:
            status_code = 502

            @staticmethod
            def json() -> dict[str, str]:
                raise ValueError("not json")

        _patch_osrm_probe(monkeypatch, _HtmlResponse())

        assert app.get("/healthz").json()["checks"]["osrm_reachable"] is False
