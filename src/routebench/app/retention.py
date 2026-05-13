"""Retention job — cleans up old session artifacts from storage."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta

import structlog

from routebench.infra.storage.base import StorageBackend

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class RetentionJob:
    """Background task that periodically cleans expired sessions."""

    def __init__(
        self,
        storage: StorageBackend,
        session_ttl_hours: int = 72,
        telemetry_ttl_hours: int = 720,
        interval_seconds: int = 3600,
    ) -> None:
        self._storage = storage
        self._session_ttl = timedelta(hours=session_ttl_hours)
        self._telemetry_ttl = timedelta(hours=telemetry_ttl_hours)
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.cleanup()
            except Exception:
                logger.exception("retention_job_error")
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def cleanup(self) -> int:
        """Run a single cleanup cycle. Returns number of sessions cleaned."""
        now = datetime.now(UTC)
        session_ids = await self._storage.list_sessions()
        cleaned = 0

        for sid in session_ids:
            try:
                data = await self._storage.read(sid, "status.json")
                status = json.loads(data)
                updated_at = datetime.fromisoformat(status.get("updated_at", ""))

                if now - updated_at > self._session_ttl:
                    # Check if telemetry should be preserved
                    if now - updated_at <= self._telemetry_ttl:
                        # Keep telemetry.json, delete everything else
                        # We re-write status.json to mark as cleaned
                        status["state"] = "expired"
                        await self._storage.write(
                            sid,
                            "status.json",
                            json.dumps(status, default=str).encode(),
                        )
                    else:
                        await self._storage.delete_session(sid)
                    cleaned += 1

            except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
                continue

        if cleaned > 0:
            logger.info("retention_cleanup", cleaned=cleaned)
        return cleaned
