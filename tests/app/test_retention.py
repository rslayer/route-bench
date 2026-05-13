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
