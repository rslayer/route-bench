"""Session worker — processes queued analysis jobs."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from routebench.app.budget import BudgetTracker
from routebench.app.pipeline import PipelineDeps, run_session
from routebench.app.sessions import (
    SessionError,
    SessionRegistry,
    SessionState,
)
from routebench.app.telemetry_sink import TelemetrySink
from routebench.core.config import AnalysisConfig
from routebench.infra.telemetry import Telemetry

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass
class JobRequest:
    """A queued analysis job."""

    session_id: str
    upload_data: bytes
    config: AnalysisConfig


class SessionWorker:
    """Single-concurrency worker that processes jobs from an asyncio.Queue."""

    def __init__(
        self,
        deps: PipelineDeps,
        registry: SessionRegistry,
        max_queue_depth: int = 5,
        job_timeout_seconds: int = 600,
        telemetry_sink: TelemetrySink | None = None,
        budget_tracker: BudgetTracker | None = None,
    ) -> None:
        self._deps = deps
        self._registry = registry
        self._queue: asyncio.Queue[JobRequest] = asyncio.Queue(maxsize=max_queue_depth)
        self._job_timeout = job_timeout_seconds
        self._telemetry_sink = telemetry_sink
        self._budget_tracker = budget_tracker
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def is_full(self) -> bool:
        return self._queue.full()

    async def enqueue(self, job: JobRequest) -> bool:
        """Enqueue a job. Returns False if queue is full."""
        try:
            self._queue.put_nowait(job)
            return True
        except asyncio.QueueFull:
            return False

    def start(self) -> None:
        """Start the background worker loop."""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Gracefully stop the worker."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        """Main worker loop — pulls jobs and processes them."""
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            logger.info("job_started", session_id=job.session_id)
            try:
                await self._process_job(job)
            except Exception:
                logger.exception("job_unhandled_error", session_id=job.session_id)
                self._registry.update(
                    job.session_id,
                    state="failed",
                    progress_pct=0,
                    stage_detail="Internal error",
                    error=SessionError(code="INTERNAL_ERROR", message="Unhandled exception"),
                )
                await self._registry.persist(job.session_id)
                self._registry.remove_active(job.session_id)

    async def _process_job(self, job: JobRequest) -> None:
        """Process a single job with timeout."""
        session_id = job.session_id

        async def on_progress(state: SessionState, pct: int, detail: str) -> None:
            self._registry.update(session_id, state=state, progress_pct=pct, stage_detail=detail)
            await self._registry.persist(session_id)

        # Write upload to temp file
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(job.upload_data)
            upload_path = Path(f.name)

        session_telemetry = Telemetry(session_id=session_id)

        try:
            result = await asyncio.wait_for(
                run_session(
                    session_id=session_id,
                    upload_path=upload_path,
                    config=job.config,
                    deps=self._deps,
                    telemetry=session_telemetry,
                    on_progress=on_progress,
                ),
                timeout=self._job_timeout,
            )

            # Flush telemetry and record spend
            if self._telemetry_sink is not None:
                cost_usd = await self._telemetry_sink.flush(session_id, session_telemetry)
                if self._budget_tracker is not None:
                    self._budget_tracker.record_spend(cost_usd)

            if result.state == "succeeded":
                self._registry.update(
                    session_id,
                    state="succeeded",
                    progress_pct=100,
                    stage_detail="Report ready",
                    artifacts=result.artifacts,
                    cost=result.cost,
                )
            else:
                self._registry.update(
                    session_id,
                    state="failed",
                    stage_detail=result.error_message or "Pipeline failed",
                    error=SessionError(
                        code="PIPELINE_ERROR",
                        message=result.error_message or "Unknown error",
                    ),
                )

        except TimeoutError:
            logger.error("job_timeout", session_id=session_id, timeout=self._job_timeout)
            self._registry.update(
                session_id,
                state="failed",
                stage_detail=f"Job timed out after {self._job_timeout}s",
                error=SessionError(
                    code="JOB_TIMEOUT",
                    message=f"Job exceeded {self._job_timeout}s timeout",
                ),
            )
        finally:
            await self._registry.persist(session_id)
            self._registry.remove_active(session_id)
            # Clean up temp file
            upload_path.unlink(missing_ok=True)
            # Also persist upload to storage
            try:
                await self._deps.storage.write(session_id, "upload.csv", job.upload_data)
            except Exception:
                logger.exception("upload_persist_error", session_id=session_id)

        logger.info("job_completed", session_id=session_id)
