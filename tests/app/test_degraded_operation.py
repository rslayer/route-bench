"""What a user gets when the infrastructure underneath is broken.

RouteBench is meant to stand on its own. These pin two behaviours a public site
cannot do without:

  - an unreachable routing backend degrades to labelled estimates with the
    quality grade withheld, rather than failing every upload;
  - an unexpected error still leaves the session in a TERMINAL state, rather
    than pinned at "analyzing" while a browser polls forever.
"""

from __future__ import annotations

import tempfile
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from routebench.agent.client import LLMClient, LLMResponse
from routebench.agent.orchestrator import AnalysisOrchestrator
from routebench.app.api.app import create_app
from routebench.core.config import Settings
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.fallback import FallbackMatrixProvider
from routebench.infra.matrix.haversine import HaversineMatrixProvider
from routebench.infra.matrix.osrm import OSRMMatrixProvider

_CSV = (
    b"route_id,stop_sequence,latitude,longitude\n"
    b"R-001,0,32.7767,-96.7970\n"
    b"R-001,1,32.7800,-96.8000\n"
    b"R-001,2,32.7850,-96.8050\n"
)

# A port nothing is listening on: OSRM is unreachable, not merely slow.
_DEAD_OSRM = "http://127.0.0.1:59999"


def _settings() -> Settings:
    return Settings(
        anthropic_api_key="test-key",
        osrm_host=_DEAD_OSRM,
        storage_path=tempfile.mkdtemp(),
    )


def _build_fleet() -> Fleet:
    stops = [
        Stop(
            route_id="R-001",
            stop_sequence=i,
            latitude=32.78 + i * 0.01,
            longitude=-96.80 - i * 0.01,
        )
        for i in (1, 2, 3)
    ]
    route = Route(
        route_id="R-001",
        stops=stops,
        depot_lat=32.7767,
        depot_lon=-96.7970,
        planned_start_time=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
    )
    return Fleet(
        routes=[route], upload_id="degraded-test", uploaded_at=datetime(2026, 1, 1, tzinfo=UTC)
    )


def _stub_llm_finishing_immediately() -> MagicMock:
    """An LLM that ends the agentic loop on its first turn.

    The orchestration itself is not what these tests are about; they care what
    the report says about the matrix underneath it.
    """
    client = MagicMock(spec=LLMClient)
    client._model = "stub-model"
    content: list[dict[str, Any]] = [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "analysis_complete",
            "input": {"summary": "done"},
        }
    ]
    client.generate.return_value = LLMResponse(
        content=content,
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
        model="stub-model",
    )
    return client


class _ExactProvider:
    """A stand-in for a healthy OSRM: real-looking numbers, approximate=False."""

    name = "exact"

    def get_matrix(self, origins, destinations, departure_time=None, origin_departure_times=None):
        n_o, n_d = len(origins), len(destinations)
        return MatrixResult(
            durations_seconds=[[300.0] * n_d for _ in range(n_o)],
            distances_meters=[[4000.0] * n_d for _ in range(n_o)],
            provider="exact",
            cached=False,
        )


class TestUnhandledErrorReachesTerminalState:
    """A stuck session is a hang with no feedback.

    _process_job's `finally` called remove_active() before the caller in
    _run_loop could mark the failure, and update() silently no-ops for a session
    that is no longer active — so "failed" was never recorded and the last
    persisted state stayed "analyzing" forever.
    """

    def test_unexpected_exception_marks_the_session_failed(self) -> None:
        app = create_app(_settings())
        with (
            patch.object(LLMClient, "generate", side_effect=RuntimeError("LLM unavailable")),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            session_id = client.post(
                "/sessions", files={"file": ("routes.csv", _CSV, "text/csv")}
            ).json()["session_id"]
            deadline = time.monotonic() + 30.0
            status: dict[str, Any] = {}
            while time.monotonic() < deadline:
                status = client.get(f"/sessions/{session_id}").json()
                if status["state"] in ("succeeded", "failed"):
                    break
                time.sleep(0.1)

        assert status["state"] == "failed", (
            f"session never reached a terminal state (got {status.get('state')!r} at "
            f"{status.get('progress_pct')}%); a polling client would wait forever"
        )
        assert status["error"]["code"] == "INTERNAL_ERROR"

    def test_updating_an_inactive_session_is_a_no_op(self) -> None:
        """The root enabler, pinned: update() returns None for a session that is
        not active, and every caller ignores the return value. It now also logs,
        so a lost state transition is at least visible."""
        from routebench.app.sessions import SessionRegistry

        registry = SessionRegistry(storage=MagicMock())
        assert registry.update("never-existed", state="failed") is None


class TestRoutingBackendUnavailable:
    """OSRM down must not take the whole site down with it."""

    def test_falls_back_to_estimates(self) -> None:
        provider = FallbackMatrixProvider(
            primary=OSRMMatrixProvider(host=_DEAD_OSRM),
            fallback=HaversineMatrixProvider(),
        )
        result = provider.get_matrix([(32.7767, -96.7970)], [(32.7800, -96.8000)])

        assert result.approximate is True
        assert result.durations_seconds[0][0] > 0

    def test_app_wires_the_fallback_by_default(self) -> None:
        """A fallback that create_app does not install protects nothing."""
        app = create_app(_settings())
        assert isinstance(app.state.deps.matrix_provider, FallbackMatrixProvider)


class TestGradeWithheldOnApproximateMatrix:
    """Every grade dimension is a function of time or distance, so grading
    guessed travel times would produce a letter that looks exactly as
    authoritative as a real one."""

    def test_grade_withheld_and_flag_set_when_approximate(self) -> None:
        orch = AnalysisOrchestrator(
            client=_stub_llm_finishing_immediately(),
            matrix_provider=HaversineMatrixProvider(),
        )
        report = orch.run(_build_fleet())

        assert report.matrix_approximate is True
        assert report.grade is None, "a grade was published from estimated travel times"

    def test_grade_is_produced_on_a_real_matrix(self) -> None:
        """The control: withholding must be caused by the approximate flag, not
        by the grading path being broken generally."""
        orch = AnalysisOrchestrator(
            client=_stub_llm_finishing_immediately(),
            matrix_provider=_ExactProvider(),
        )
        report = orch.run(_build_fleet())

        assert report.matrix_approximate is False
        assert report.grade is not None

    def test_rendered_report_explains_the_missing_score(self) -> None:
        """A report that just omits its headline reads as broken. The reason has
        to appear where the number would have been."""
        from routebench.report.document import ReportDocument

        orch = AnalysisOrchestrator(
            client=_stub_llm_finishing_immediately(),
            matrix_provider=HaversineMatrixProvider(),
        )
        report = orch.run(_build_fleet())
        doc = ReportDocument(report)
        html = doc.render({slot.slot_id: "Prose." for slot in doc.identify_prose_slots()})

        assert 'id="quality-score"' in html, "the score section vanished entirely"
        assert "Withheld" in html
        assert "routing service was unavailable" in html

    def test_the_rest_of_the_report_still_stands(self) -> None:
        """Withholding the grade must not gut the report — the user still gets
        their routes and metrics, which is the point of degrading at all."""
        orch = AnalysisOrchestrator(
            client=_stub_llm_finishing_immediately(),
            matrix_provider=HaversineMatrixProvider(),
        )
        report = orch.run(_build_fleet())

        assert report.fleet_metrics.total_routes == 1
        assert report.fleet_metrics.total_stops == 3
        assert report.route_metrics["R-001"].total_distance_miles > 0
