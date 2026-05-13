"""Tests for the SessionWorker."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from routebench.app.pipeline import PipelineDeps, SessionResult
from routebench.app.sessions import SessionRegistry
from routebench.app.worker import JobRequest, SessionWorker
from routebench.core.config import AnalysisConfig, Settings
from routebench.infra.storage.local import LocalStorageBackend


def _make_valid_csv_bytes() -> bytes:
    lines = [
        "route_id,stop_sequence,latitude,longitude",
        "R-001,0,32.825,-96.775",
        "R-001,1,32.830,-96.770",
        "R-001,2,32.835,-96.765",
    ]
    return "\n".join(lines).encode()


@pytest.fixture()
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(base_path=str(tmp_path / "sessions"))


@pytest.fixture()
def deps(storage: LocalStorageBackend) -> PipelineDeps:
    return PipelineDeps(
        matrix_provider=MagicMock(),
        storage=storage,
        llm_client=MagicMock(),
        settings=Settings(storage_path=str(storage._base)),
    )


@pytest.fixture()
def registry(storage: LocalStorageBackend) -> SessionRegistry:
    return SessionRegistry(storage=storage)


class TestSessionWorker:
    """Tests for worker queue and job processing."""

    @pytest.mark.asyncio()
    async def test_enqueue_and_is_full(self, deps: PipelineDeps, registry: SessionRegistry) -> None:
        """Test queue capacity checking."""
        worker = SessionWorker(deps=deps, registry=registry, max_queue_depth=2)
        csv_data = _make_valid_csv_bytes()

        registry.create("session-1")
        job1 = JobRequest(session_id="session-1", upload_data=csv_data, config=AnalysisConfig())
        assert await worker.enqueue(job1) is True
        assert worker.queue_size == 1

        registry.create("session-2")
        job2 = JobRequest(session_id="session-2", upload_data=csv_data, config=AnalysisConfig())
        assert await worker.enqueue(job2) is True
        assert worker.is_full is True

        # Third should fail
        registry.create("session-3")
        job3 = JobRequest(session_id="session-3", upload_data=csv_data, config=AnalysisConfig())
        assert await worker.enqueue(job3) is False

    @pytest.mark.asyncio()
    async def test_worker_starts_and_stops(
        self, deps: PipelineDeps, registry: SessionRegistry
    ) -> None:
        """Worker should start and stop cleanly."""
        worker = SessionWorker(deps=deps, registry=registry)
        worker.start()
        assert worker._running is True
        await worker.stop()
        assert worker._running is False

    @pytest.mark.asyncio()
    async def test_timeout_marks_failed(
        self, deps: PipelineDeps, registry: SessionRegistry, storage: LocalStorageBackend
    ) -> None:
        """Jobs exceeding timeout should be marked failed."""
        worker = SessionWorker(
            deps=deps, registry=registry, max_queue_depth=5, job_timeout_seconds=1
        )

        csv_data = _make_valid_csv_bytes()
        session_id = "timeout-test"
        registry.create(session_id)

        # Mock the pipeline to sleep longer than timeout
        async def slow_pipeline(*args: object, **kwargs: object) -> SessionResult:
            await asyncio.sleep(10)
            return SessionResult(session_id=session_id, state="succeeded")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("routebench.app.worker.run_session", slow_pipeline)
            job = JobRequest(session_id=session_id, upload_data=csv_data, config=AnalysisConfig())
            worker.start()
            await worker.enqueue(job)

            # Wait for job to process and timeout
            await asyncio.sleep(3)
            await worker.stop()

        # Check status was persisted as failed
        status = await registry.get(session_id)
        if status is not None:
            assert status.state == "failed"
            assert status.error is not None
            err_text = status.error.code.lower() + status.error.message.lower()
            assert "timeout" in err_text
