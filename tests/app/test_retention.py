"""Tests for retention job."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from routebench.app.retention import RetentionJob
from routebench.infra.storage.local import LocalStorageBackend


@pytest.fixture()
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(base_path=str(tmp_path / "sessions"))


class TestRetentionJob:
    """Tests for retention cleanup."""

    @pytest.mark.asyncio()
    async def test_cleanup_old_sessions(self, storage: LocalStorageBackend) -> None:
        """Sessions older than TTL should be cleaned up."""
        # Create a session with old timestamp
        old_time = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        status = {
            "session_id": "old-session",
            "state": "succeeded",
            "progress_pct": 100,
            "stage_detail": "done",
            "created_at": old_time,
            "updated_at": old_time,
        }
        await storage.write("old-session", "status.json", json.dumps(status).encode())
        await storage.write("old-session", "report.html", b"<html></html>")

        retention = RetentionJob(storage=storage, session_ttl_hours=72)
        cleaned = await retention.cleanup()
        assert cleaned == 1

    @pytest.mark.asyncio()
    async def test_expiry_actually_deletes_customer_data(
        self, storage: LocalStorageBackend
    ) -> None:
        """Past the session TTL, everything carrying customer data is gone.

        The original test asserted only that `cleanup` returned 1, which it did
        while deleting nothing at all — the artifact loop was a bare `pass`. So
        this asserts on the bytes, not the count: the promise the UI makes to
        users is about the upload being deleted, and only storage can confirm
        that.
        """
        old_time = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        await storage.write(
            "old-session",
            "status.json",
            json.dumps(
                {
                    "session_id": "old-session",
                    "state": "succeeded",
                    "progress_pct": 100,
                    "stage_detail": "done",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ).encode(),
        )
        for fname, body in (
            ("upload.csv", b"route_id,address\nR1,1 Main St"),
            ("report.html", b"<html>1 Main St</html>"),
            ("report.pdf", b"%PDF-1.4"),
            ("analysis.json", b'{"stops": []}'),
            ("routes.geojson", b'{"type": "FeatureCollection"}'),
            ("telemetry.json", b'{"llm_calls": []}'),
        ):
            await storage.write("old-session", fname, body)

        assert await RetentionJob(storage=storage, session_ttl_hours=72).cleanup() == 1

        for gone in (
            "upload.csv",
            "report.html",
            "report.pdf",
            "analysis.json",
            "routes.geojson",
        ):
            assert not await storage.exists("old-session", gone), f"{gone} survived expiry"

        # Status and telemetry survive to the longer telemetry TTL: neither
        # holds customer data, and status is what lets the link say "expired"
        # rather than 404.
        assert await storage.exists("old-session", "status.json")
        assert await storage.exists("old-session", "telemetry.json")
        status = json.loads(await storage.read("old-session", "status.json"))
        assert status["state"] == "expired"

    @pytest.mark.asyncio()
    async def test_cleanup_is_idempotent(self, storage: LocalStorageBackend) -> None:
        """Re-running over an already-expired session must not error.

        Retention sweeps the same sessions every hour between the session TTL
        and the telemetry TTL, so the second pass finds the artifacts already
        deleted. `delete_file` returning False for missing is what makes that
        safe rather than a raised FileNotFoundError that aborts the sweep for
        every session after this one.
        """
        old_time = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
        await storage.write(
            "old-session",
            "status.json",
            json.dumps(
                {
                    "session_id": "old-session",
                    "state": "succeeded",
                    "created_at": old_time,
                    "updated_at": old_time,
                }
            ).encode(),
        )
        await storage.write("old-session", "upload.csv", b"data")

        job = RetentionJob(storage=storage, session_ttl_hours=72)
        assert await job.cleanup() == 1
        assert await job.cleanup() == 1  # no raise, still counted
        assert not await storage.exists("old-session", "upload.csv")

    @pytest.mark.asyncio()
    async def test_skip_fresh_sessions(self, storage: LocalStorageBackend) -> None:
        """Fresh sessions should not be cleaned."""
        now = datetime.now(UTC).isoformat()
        status = {
            "session_id": "fresh-session",
            "state": "succeeded",
            "progress_pct": 100,
            "stage_detail": "done",
            "created_at": now,
            "updated_at": now,
        }
        await storage.write("fresh-session", "status.json", json.dumps(status).encode())

        retention = RetentionJob(storage=storage, session_ttl_hours=72)
        cleaned = await retention.cleanup()
        assert cleaned == 0

    @pytest.mark.asyncio()
    async def test_start_and_stop(self, storage: LocalStorageBackend) -> None:
        """Retention job should start and stop cleanly."""
        retention = RetentionJob(storage=storage, interval_seconds=1)
        retention.start()
        assert retention._running is True
        await retention.stop()
        assert retention._running is False
