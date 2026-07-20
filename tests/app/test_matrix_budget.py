"""The daily matrix-spend cap.

On the metered engine (Google), once the day's estimated matrix spend reaches
the cap, further runs must degrade to haversine estimates (grade withheld)
instead of billing more — and a run that did use the engine must record its
estimated spend so the next run sees it. These drive run_session directly with a
call-recording stand-in for Google.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from routebench.agent.client import LLMClient
from routebench.app.budget import BudgetTracker
from routebench.app.pipeline import PipelineDeps, run_session
from routebench.core.config import AnalysisConfig, Settings
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.storage.local import LocalStorageBackend

_CSV = (
    b"route_id,stop_sequence,latitude,longitude\n"
    b"R-001,0,32.7767,-96.7970\n"
    b"R-001,1,32.7800,-96.8000\n"
    b"R-001,2,32.7850,-96.8050\n"
)


class _RecordingGoogle:
    """Stands in for the Google engine: exact (approximate=False) and counts calls."""

    name = "google"
    is_time_aware = True

    def __init__(self) -> None:
        self.calls = 0

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        self.calls += 1
        n_o, n_d = len(origins), len(destinations)
        return MatrixResult(
            durations_seconds=[[300.0] * n_d for _ in range(n_o)],
            distances_meters=[[4000.0] * n_d for _ in range(n_o)],
            provider="google",
            cached=False,
            cost_estimate=0.5,
            approximate=False,
        )


def _deps(tmp_path: Path, budget: float, provider: _RecordingGoogle) -> PipelineDeps:
    storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
    return PipelineDeps(
        matrix_provider=provider,  # type: ignore[arg-type]
        storage=storage,
        llm_client=LLMClient(api_key=""),  # available=False → deterministic path, no LLM calls
        settings=Settings(
            _env_file=None,  # type: ignore[call-arg]
            matrix_engine="google",
            google_maps_api_key="k",
            daily_matrix_budget_usd=budget,
            storage_path=str(tmp_path / "sessions"),
            anthropic_api_key="",
        ),
        matrix_budget_tracker=BudgetTracker(
            storage=storage, daily_budget_usd=budget, ledger_prefix="matrix-ledger"
        ),
    )


def _fast_config() -> AnalysisConfig:
    # Tiny solver limits: these tests are about the budget decision, not
    # solver quality, and OR-Tools otherwise runs to its full time limit.
    return AnalysisConfig(route_benchmark_time_limit_s=1, fleet_benchmark_time_limit_s=1)


def _write_csv(tmp_path: Path) -> Path:
    p = tmp_path / "routes.csv"
    p.write_bytes(_CSV)
    return p


class TestMatrixBudgetDegrade:
    def test_over_budget_run_uses_haversine_not_google(self, tmp_path: Path) -> None:
        provider = _RecordingGoogle()
        deps = _deps(tmp_path, budget=0.01, provider=provider)
        # Pre-spend the day over the cap.
        assert deps.matrix_budget_tracker is not None
        asyncio.run(deps.matrix_budget_tracker.record_spend(0.02, session_id="earlier"))

        result = asyncio.run(run_session("s1", _write_csv(tmp_path), _fast_config(), deps))

        assert result.state == "succeeded"
        # The engine was never called: the run degraded to haversine up front.
        assert provider.calls == 0
        # And no further spend was recorded — a degraded run bills nothing more.
        spend = asyncio.run(deps.matrix_budget_tracker.today_spend())
        assert spend == pytest.approx(0.02)

    def test_under_budget_run_uses_google_and_records_spend(self, tmp_path: Path) -> None:
        provider = _RecordingGoogle()
        deps = _deps(tmp_path, budget=100.0, provider=provider)

        result = asyncio.run(run_session("s2", _write_csv(tmp_path), _fast_config(), deps))

        assert result.state == "succeeded"
        assert provider.calls > 0  # the engine was actually used
        assert deps.matrix_budget_tracker is not None
        spend = asyncio.run(deps.matrix_budget_tracker.today_spend())
        assert spend > 0  # this run's estimated cost was recorded for the day

    def test_no_cap_never_degrades_or_records(self, tmp_path: Path) -> None:
        """budget 0 = off: the engine is always used and nothing is metered."""
        provider = _RecordingGoogle()
        deps = _deps(tmp_path, budget=0.0, provider=provider)

        result = asyncio.run(run_session("s3", _write_csv(tmp_path), _fast_config(), deps))

        assert result.state == "succeeded"
        assert provider.calls > 0
        assert deps.matrix_budget_tracker is not None
        assert asyncio.run(deps.matrix_budget_tracker.today_spend()) == 0.0


class TestLedgerSeparation:
    def test_matrix_and_llm_ledgers_are_independent(self, tmp_path: Path) -> None:
        """The whole point of the prefix: matrix spend must not read as LLM
        spend, or the two caps would corrupt each other."""
        storage = LocalStorageBackend(base_path=str(tmp_path / "sessions"))
        llm = BudgetTracker(storage=storage, daily_budget_usd=10.0)  # default "ledger"
        matrix = BudgetTracker(
            storage=storage, daily_budget_usd=10.0, ledger_prefix="matrix-ledger"
        )

        asyncio.run(llm.record_spend(3.0, session_id="a"))
        asyncio.run(matrix.record_spend(7.0, session_id="a"))

        assert asyncio.run(llm.today_spend()) == pytest.approx(3.0)
        assert asyncio.run(matrix.today_spend()) == pytest.approx(7.0)
