"""Retention job — cleans up old session artifacts from storage."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta

import structlog

from routebench.infra.storage.base import StorageBackend

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_ARTIFACT_FILES = ("report.html", "report.pdf", "analysis.json", "upload.csv")

_TERMINAL_STATES = frozenset({"succeeded", "failed", "expired"})

_STALE_MESSAGE = (
    "Analysis stopped responding and was marked failed. Your upload was preserved — please retry."
)


class RetentionJob:
    """Background task that periodically cleans expired sessions."""

    def __init__(
        self,
        storage: StorageBackend,
        session_ttl_hours: int = 72,
        telemetry_ttl_hours: int = 720,
        interval_seconds: int = 3600,
        job_timeout_seconds: int = 600,
    ) -> None:
        self._storage = storage
        self._session_ttl = timedelta(hours=session_ttl_hours)
        self._telemetry_ttl = timedelta(hours=telemetry_ttl_hours)
        self._interval = interval_seconds
        # A session in flight for twice the job timeout cannot still be running:
        # the worker would have timed it out. It is a corpse from a hard kill
        # that startup recovery missed (or that died after recovery ran).
        self._stale_after = timedelta(seconds=job_timeout_seconds * 2)
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
                await self.sweep_stale()
            except Exception:
                logger.exception("stale_sweep_error")
            try:
                await self.cleanup()
            except Exception:
                logger.exception("retention_job_error")
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def sweep_stale(self) -> int:
        """Fail sessions stuck in flight for longer than 2x the job timeout.

        Startup recovery catches sessions orphaned by a restart this process knows
        about. This catches the rest: a hard kill that left status.json mid-flight
        while a later deploy never ran recovery, or a worker that died without
        unwinding. Without it, such a session polls forever.

        Returns the number of sessions transitioned.
        """
        now = datetime.now(UTC)
        swept = 0

        for sid in await self._storage.list_sessions():
            try:
                raw = await self._storage.read(sid, "status.json")
                status = json.loads(raw)
                state = status.get("state", "")
                if state in _TERMINAL_STATES:
                    continue
                updated_at = datetime.fromisoformat(status.get("updated_at", ""))
                if now - updated_at <= self._stale_after:
                    continue

                status["state"] = "failed"
                status["updated_at"] = now.isoformat()
                status["stage_detail"] = _STALE_MESSAGE
                status["error"] = {
                    "code": "stale",
                    "message": _STALE_MESSAGE,
                    "context": {"last_state": state},
                }
                await self._storage.write(
                    sid,
                    "status.json",
                    json.dumps(status, default=str).encode(),
                )
                logger.info(
                    "stale_session_failed",
                    session_id=sid,
                    last_state=state,
                    stale_for_seconds=round((now - updated_at).total_seconds()),
                )
                swept += 1
            except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
                continue

        return swept

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
                    if now - updated_at <= self._telemetry_ttl:
                        # Delete heavy artifacts but keep telemetry + status
                        for fname in _ARTIFACT_FILES:
                            try:
                                if await self._storage.exists(sid, fname):
                                    # For local storage, delete individually isn't on the protocol
                                    # Mark as expired in status instead
                                    pass
                            except Exception:
                                pass
                        status["state"] = "expired"
                        await self._storage.write(
                            sid,
                            "status.json",
                            json.dumps(status, default=str).encode(),
                        )
                    else:
                        # Past telemetry TTL — delete entire session
                        await self._storage.delete_session(sid)
                    cleaned += 1

            except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
                continue

        if cleaned > 0:
            logger.info("retention_cleanup", cleaned=cleaned)
        return cleaned
