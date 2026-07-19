"""Session worker — processes queued analysis jobs."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog
from pydantic import ValidationError

from routebench.app.budget import BudgetTracker
from routebench.app.pipeline import PipelineDeps, run_session
from routebench.app.sessions import (
    SessionError,
    SessionRegistry,
    SessionState,
    SessionStatus,
)
from routebench.app.telemetry_sink import TelemetrySink
from routebench.core.config import AnalysisConfig
from routebench.infra.telemetry import Telemetry

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# A session in any other state was interrupted: the pipeline moves through
# validating -> analyzing -> writing -> rendering, and a crash can strand a
# session in any of them, not just the two the happy path spends longest in.
TERMINAL_STATES: frozenset[str] = frozenset({"succeeded", "failed", "expired"})

INTERRUPTED_MESSAGE = (
    "Analysis was interrupted by a server restart before it finished. "
    "Your upload was preserved — please retry."
)


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

    async def recover(self) -> dict[str, int]:
        """Reconcile sessions left in flight by a restart.

        Queued sessions never started, so they are re-enqueued and run normally.
        Anything past `queued` was mid-pipeline when the process died; there is
        no resumable checkpoint, so it is failed with a message telling the user
        their upload survived and they may retry.

        Returns counts per outcome, for logging and tests.
        """
        counts = {"requeued": 0, "interrupted": 0}
        try:
            session_ids = await self._deps.storage.list_sessions()
        except Exception:
            logger.exception("recovery_list_failed")
            return counts

        for sid in session_ids:
            try:
                raw = await self._deps.storage.read(sid, "status.json")
                status = SessionStatus.model_validate_json(raw)
            except (FileNotFoundError, ValidationError, ValueError):
                continue

            if status.state in TERMINAL_STATES:
                continue

            if status.state == "queued" and await self._requeue(status):
                counts["requeued"] += 1
                continue

            await self._fail_session(
                status,
                code="interrupted_by_restart",
                message=INTERRUPTED_MESSAGE,
            )
            counts["interrupted"] += 1

        if counts["requeued"] or counts["interrupted"]:
            logger.info("startup_recovery", **counts)
        return counts

    async def _requeue(self, status: SessionStatus) -> bool:
        """Re-enqueue a session that never started. False if it cannot be rebuilt."""
        sid = status.session_id
        try:
            upload_data = await self._deps.storage.read(sid, "upload.csv")
            config_raw = await self._deps.storage.read(sid, "config.json")
            config = AnalysisConfig.model_validate_json(config_raw)
        except (FileNotFoundError, ValidationError, ValueError):
            # Re-running with a default config would quietly produce a different
            # report than the caller asked for, so fail instead of guessing.
            logger.warning("recovery_requeue_incomplete", session_id=sid)
            return False

        self._registry.restore(status)
        enqueued = await self.enqueue(
            JobRequest(session_id=sid, upload_data=upload_data, config=config)
        )
        if not enqueued:
            self._registry.remove_active(sid)
            logger.warning("recovery_requeue_queue_full", session_id=sid)
            return False

        logger.info("recovery_requeued", session_id=sid)
        return True

    async def _fail_session(self, status: SessionStatus, *, code: str, message: str) -> None:
        """Mark a recovered session failed and persist it."""
        self._registry.restore(status)
        self._registry.update(
            status.session_id,
            state="failed",
            stage_detail=message,
            error=SessionError(code=code, message=message),
        )
        await self._registry.persist(status.session_id)
        self._registry.remove_active(status.session_id)
        logger.info("recovery_failed_session", session_id=status.session_id, code=code)

    async def _run_loop(self) -> None:
        """Main worker loop — pulls jobs and processes them."""
        await self.recover()
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

        async def on_progress(
            state: SessionState, pct: int, detail: str, seconds_remaining: int | None = None
        ) -> None:
            self._registry.update(
                session_id,
                state=state,
                progress_pct=pct,
                stage_detail=detail,
                seconds_remaining=seconds_remaining,
            )
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
                    await self._budget_tracker.record_spend(
                        cost_usd,
                        session_id=session_id,
                        input_tokens=sum(c.input_tokens for c in session_telemetry.llm_calls),
                        output_tokens=sum(c.output_tokens for c in session_telemetry.llm_calls),
                    )

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
        except Exception:
            # Marked terminal HERE, while the session is still in `_active`.
            #
            # This used to be handled only by the caller in _run_loop, which
            # runs after this function's `finally` has already called
            # remove_active(). update() silently returns None for a session that
            # is no longer active, so the failure was never recorded: the last
            # persisted status stayed "analyzing" and the session hung in a
            # non-terminal state forever. A browser polling /sessions/{id} — or
            # holding the SSE stream — would wait for a completion that could
            # never arrive. Any unexpected error must still leave the user with
            # a terminal answer.
            logger.exception("job_unhandled_error", session_id=session_id)
            self._registry.update(
                session_id,
                state="failed",
                progress_pct=0,
                stage_detail="Internal error",
                error=SessionError(code="INTERNAL_ERROR", message="Unhandled exception"),
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
