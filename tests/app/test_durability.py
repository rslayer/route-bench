"""Phase 10.5 Part C: state that survives a restart.

Budget, queue, and session state used to live only in memory, so a redeploy
handed the day a fresh budget and left in-flight sessions polling forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from routebench.app.budget import BudgetTracker, ledger_key
from routebench.app.retention import RetentionJob
from routebench.app.sessions import SessionRegistry, SessionStatus
from routebench.app.worker import JobRequest, SessionWorker
from routebench.core.config import URBAN_US_PROFILE, AnalysisConfig
from routebench.infra.storage.local import LocalStorageBackend


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(base_path=str(tmp_path / "sessions"))


class TestStorageObjectApi:
    """Non-session objects live outside the session namespace."""

    @pytest.mark.asyncio()
    async def test_append_then_read(self, storage: LocalStorageBackend) -> None:
        await storage.append_object("ledger/2026-07-15.jsonl", b"one\n")
        await storage.append_object("ledger/2026-07-15.jsonl", b"two\n")
        assert await storage.read_object("ledger/2026-07-15.jsonl") == b"one\ntwo\n"

    @pytest.mark.asyncio()
    async def test_append_creates_missing_object(self, storage: LocalStorageBackend) -> None:
        await storage.append_object("ledger/new.jsonl", b"x\n")
        assert await storage.read_object("ledger/new.jsonl") == b"x\n"

    @pytest.mark.asyncio()
    async def test_missing_object_raises(self, storage: LocalStorageBackend) -> None:
        with pytest.raises(FileNotFoundError):
            await storage.read_object("ledger/nope.jsonl")

    @pytest.mark.asyncio()
    async def test_objects_are_not_sessions(self, storage: LocalStorageBackend) -> None:
        """The ledger must never show up as a session to admin or retention."""
        await storage.append_object("ledger/2026-07-15.jsonl", b"x\n")
        await storage.write("abc123", "status.json", b"{}")
        assert await storage.list_sessions() == ["abc123"]


class TestBudgetLedger:
    """The daily cap is durable."""

    @pytest.mark.asyncio()
    async def test_spend_survives_a_new_tracker(self, storage: LocalStorageBackend) -> None:
        """A restart builds a fresh tracker; the day's total must persist."""
        tracker = BudgetTracker(storage=storage, daily_budget_usd=10.0)
        await tracker.record_spend(4.0, session_id="s1")

        restarted = BudgetTracker(storage=storage, daily_budget_usd=10.0)
        assert await restarted.today_spend() == pytest.approx(4.0)

    @pytest.mark.asyncio()
    async def test_spend_accumulates(self, storage: LocalStorageBackend) -> None:
        tracker = BudgetTracker(storage=storage, daily_budget_usd=10.0)
        await tracker.record_spend(1.5, session_id="s1")
        await tracker.record_spend(2.25, session_id="s2")
        assert await tracker.today_spend() == pytest.approx(3.75)

    @pytest.mark.asyncio()
    async def test_empty_ledger_is_zero(self, storage: LocalStorageBackend) -> None:
        tracker = BudgetTracker(storage=storage, daily_budget_usd=10.0)
        assert await tracker.today_spend() == 0.0
        assert not await tracker.is_exceeded()

    @pytest.mark.asyncio()
    async def test_cap_trips_across_restart(self, storage: LocalStorageBackend) -> None:
        tracker = BudgetTracker(storage=storage, daily_budget_usd=5.0)
        await tracker.record_spend(6.0, session_id="s1")

        restarted = BudgetTracker(storage=storage, daily_budget_usd=5.0)
        assert await restarted.is_exceeded()
        assert restarted.rejections == 1

    @pytest.mark.asyncio()
    async def test_ledger_is_keyed_by_day(self, storage: LocalStorageBackend) -> None:
        tracker = BudgetTracker(storage=storage, daily_budget_usd=10.0)
        await tracker.record_spend(1.0, session_id="s1")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert await storage.read_object(ledger_key(today))

    @pytest.mark.asyncio()
    async def test_ledger_records_token_fields(self, storage: LocalStorageBackend) -> None:
        tracker = BudgetTracker(storage=storage, daily_budget_usd=10.0)
        await tracker.record_spend(1.0, session_id="s1", input_tokens=100, output_tokens=20)
        raw = await storage.read_object(ledger_key(datetime.now(UTC).strftime("%Y-%m-%d")))
        assert b'"input_tokens":100' in raw
        assert b'"output_tokens":20' in raw

    @pytest.mark.asyncio()
    async def test_corrupt_line_does_not_break_the_check(
        self, storage: LocalStorageBackend
    ) -> None:
        """A torn write must not take the budget check down with it."""
        key = ledger_key(datetime.now(UTC).strftime("%Y-%m-%d"))
        await storage.append_object(key, b'{"cost_usd": 2.0}\n')
        await storage.append_object(key, b'{"cost_usd": 3.0\n')  # truncated
        tracker = BudgetTracker(storage=storage, daily_budget_usd=10.0)
        assert await tracker.today_spend() == pytest.approx(2.0)


def _deps(storage: LocalStorageBackend) -> MagicMock:
    deps = MagicMock()
    deps.storage = storage
    return deps


async def _seed(storage: LocalStorageBackend, session_id: str, state: str, *, config: bool = True):
    status = SessionStatus(session_id=session_id, state=state)  # type: ignore[arg-type]
    await storage.write(session_id, "status.json", status.model_dump_json().encode())
    await storage.write(session_id, "upload.csv", b"route_id,stop_sequence,latitude,longitude\n")
    if config:
        await storage.write(session_id, "config.json", AnalysisConfig().model_dump_json().encode())


class TestStartupRecovery:
    """A restart must not strand sessions."""

    @pytest.mark.asyncio()
    async def test_queued_session_is_requeued(self, storage: LocalStorageBackend) -> None:
        await _seed(storage, "s_queued", "queued")
        worker = SessionWorker(deps=_deps(storage), registry=SessionRegistry(storage=storage))

        counts = await worker.recover()

        assert counts["requeued"] == 1
        assert worker.queue_size == 1

    @pytest.mark.asyncio()
    async def test_requeue_preserves_the_caller_config(self, storage: LocalStorageBackend) -> None:
        """Recovering with a default config would run a different analysis."""
        session_id = "s_profiled"
        status = SessionStatus(session_id=session_id, state="queued")
        await storage.write(session_id, "status.json", status.model_dump_json().encode())
        await storage.write(session_id, "upload.csv", b"x")
        await storage.write(
            session_id,
            "config.json",
            AnalysisConfig(traffic=URBAN_US_PROFILE).model_dump_json().encode(),
        )

        worker = SessionWorker(deps=_deps(storage), registry=SessionRegistry(storage=storage))
        await worker.recover()

        job: JobRequest = worker._queue.get_nowait()
        assert job.config.traffic.is_active, "recovered session lost its traffic profile"

    @pytest.mark.parametrize("state", ["validating", "analyzing", "writing", "rendering"])
    @pytest.mark.asyncio()
    async def test_in_flight_session_is_failed(
        self, storage: LocalStorageBackend, state: str
    ) -> None:
        """Every non-terminal state is unresumable, not just 'analyzing'."""
        await _seed(storage, "s_mid", state)
        registry = SessionRegistry(storage=storage)
        worker = SessionWorker(deps=_deps(storage), registry=registry)

        counts = await worker.recover()

        assert counts["interrupted"] == 1
        status = await registry.get("s_mid")
        assert status is not None
        assert status.state == "failed"
        assert status.error is not None
        assert status.error.code == "interrupted_by_restart"
        assert "retry" in status.error.message.lower()

    @pytest.mark.parametrize("state", ["succeeded", "failed", "expired"])
    @pytest.mark.asyncio()
    async def test_terminal_sessions_untouched(
        self, storage: LocalStorageBackend, state: str
    ) -> None:
        await _seed(storage, "s_done", state)
        worker = SessionWorker(deps=_deps(storage), registry=SessionRegistry(storage=storage))

        counts = await worker.recover()

        assert counts == {"requeued": 0, "interrupted": 0}
        assert worker.queue_size == 0

    @pytest.mark.asyncio()
    async def test_queued_without_config_fails_rather_than_guessing(
        self, storage: LocalStorageBackend
    ) -> None:
        await _seed(storage, "s_noconfig", "queued", config=False)
        registry = SessionRegistry(storage=storage)
        worker = SessionWorker(deps=_deps(storage), registry=registry)

        counts = await worker.recover()

        assert counts["requeued"] == 0
        assert counts["interrupted"] == 1
        status = await registry.get("s_noconfig")
        assert status is not None and status.state == "failed"

    @pytest.mark.asyncio()
    async def test_upload_is_preserved_for_retry(self, storage: LocalStorageBackend) -> None:
        """The failure message promises the upload survived; keep that promise."""
        await _seed(storage, "s_mid", "analyzing")
        worker = SessionWorker(deps=_deps(storage), registry=SessionRegistry(storage=storage))
        await worker.recover()
        assert await storage.exists("s_mid", "upload.csv")


class TestStaleJanitor:
    """Corpses that recovery missed get swept."""

    async def _seed_aged(
        self, storage: LocalStorageBackend, session_id: str, state: str, age_seconds: int
    ) -> None:
        status = SessionStatus(session_id=session_id, state=state)  # type: ignore[arg-type]
        status.updated_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        await storage.write(session_id, "status.json", status.model_dump_json().encode())

    @pytest.mark.asyncio()
    async def test_stale_in_flight_session_is_failed(self, storage: LocalStorageBackend) -> None:
        await self._seed_aged(storage, "s_stale", "analyzing", age_seconds=2000)
        job = RetentionJob(storage=storage, job_timeout_seconds=600)

        assert await job.sweep_stale() == 1

        status = SessionStatus.model_validate_json(await storage.read("s_stale", "status.json"))
        assert status.state == "failed"
        assert status.error is not None
        assert status.error.code == "stale"

    @pytest.mark.asyncio()
    async def test_recent_in_flight_session_is_left_alone(
        self, storage: LocalStorageBackend
    ) -> None:
        """A session inside the timeout may still be running."""
        await self._seed_aged(storage, "s_running", "analyzing", age_seconds=60)
        job = RetentionJob(storage=storage, job_timeout_seconds=600)

        assert await job.sweep_stale() == 0

        status = SessionStatus.model_validate_json(await storage.read("s_running", "status.json"))
        assert status.state == "analyzing"

    @pytest.mark.asyncio()
    async def test_stale_threshold_is_twice_the_job_timeout(
        self, storage: LocalStorageBackend
    ) -> None:
        await self._seed_aged(storage, "s_edge", "analyzing", age_seconds=1100)
        assert await RetentionJob(storage=storage, job_timeout_seconds=600).sweep_stale() == 0
        assert await RetentionJob(storage=storage, job_timeout_seconds=500).sweep_stale() == 1

    @pytest.mark.asyncio()
    async def test_terminal_sessions_are_not_swept(self, storage: LocalStorageBackend) -> None:
        await self._seed_aged(storage, "s_done", "succeeded", age_seconds=99999)
        assert await RetentionJob(storage=storage, job_timeout_seconds=600).sweep_stale() == 0

    @pytest.mark.asyncio()
    async def test_last_state_is_recorded(self, storage: LocalStorageBackend) -> None:
        await self._seed_aged(storage, "s_stale", "writing", age_seconds=2000)
        await RetentionJob(storage=storage, job_timeout_seconds=600).sweep_stale()
        status = SessionStatus.model_validate_json(await storage.read("s_stale", "status.json"))
        assert status.error is not None
        assert status.error.context.get("last_state") == "writing"
