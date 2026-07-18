"""The product works without a language model.

The LLM does two jobs here: it selects which deterministic analyzers to run,
and it writes the narrative. It computes nothing. So the evaluation a user
actually comes for — metrics, findings, benchmark, the quality grade — must be
reachable with no API key at all, and must survive a spent daily budget rather
than taking the service offline.
"""

from __future__ import annotations

import json
import tempfile
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from routebench.agent.client import LLMClient
from routebench.agent.orchestrator import AnalysisOrchestrator
from routebench.agent.verifier import deterministic_prose
from routebench.app.api.app import create_app
from routebench.core.config import AnalysisConfig, Settings
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult
from routebench.report.prose_slots import ProseSlot

# Solvers spend their limit in full and the deterministic path runs every
# benchmark tool, so keep the limits tiny or the suite waits minutes.
_FAST = json.dumps({"route_benchmark_time_limit_s": 1, "fleet_benchmark_time_limit_s": 1})
_FAST_CONFIG = AnalysisConfig(route_benchmark_time_limit_s=1, fleet_benchmark_time_limit_s=1)

_CSV = (
    b"route_id,stop_sequence,latitude,longitude\n"
    b"R-001,0,32.7767,-96.7970\n"
    b"R-001,1,32.7800,-96.8000\n"
    b"R-001,2,32.7850,-96.8050\n"
    b"R-002,0,32.7767,-96.7970\n"
    b"R-002,1,32.7500,-96.7500\n"
    b"R-002,2,32.7400,-96.7400\n"
)


class _HealthyMatrix:
    """Stands in for a reachable OSRM: exact numbers, not estimates.

    Needed to isolate the variable — with the haversine fallback the matrix is
    approximate and the grade is withheld for that reason, which would mask
    whether the no-LLM path can produce a grade at all.
    """

    name = "exact"

    def get_matrix(
        self,
        origins: Any,
        destinations: Any,
        departure_time: Any = None,
        origin_departure_times: Any = None,
    ) -> MatrixResult:
        return MatrixResult(
            durations_seconds=[[300.0] * len(destinations) for _ in origins],
            distances_meters=[[4000.0] * len(destinations) for _ in origins],
            provider="exact",
            cached=False,
        )


def _fleet(n_routes: int = 2, n_stops: int = 4) -> Fleet:
    routes = [
        Route(
            route_id=f"R-{r:03d}",
            stops=[
                Stop(
                    route_id=f"R-{r:03d}",
                    stop_sequence=i,
                    latitude=32.78 + i * 0.01 + r * 0.05,
                    longitude=-96.80 - i * 0.01 - r * 0.05,
                )
                for i in range(1, n_stops + 1)
            ],
            depot_lat=32.7767,
            depot_lon=-96.7970,
            planned_start_time=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        )
        for r in range(1, n_routes + 1)
    ]
    return Fleet(routes=routes, upload_id="no-llm", uploaded_at=datetime(2026, 1, 1, tzinfo=UTC))


def _run_to_completion(client: TestClient, timeout_s: float = 60.0) -> dict[str, Any]:
    session_id = client.post(
        "/sessions", files={"file": ("routes.csv", _CSV, "text/csv")}, data={"config": _FAST}
    ).json()["session_id"]
    deadline = time.monotonic() + timeout_s
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}").json()
        if status["state"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    status["_session_id"] = session_id
    return status


class TestAnalysisWithoutAnLLM:
    """No API key at all — the whole evaluation must still be produced."""

    def test_grade_is_produced_with_no_api_key(self) -> None:
        """The headline number a user comes for does not depend on the LLM."""
        orch = AnalysisOrchestrator(
            client=LLMClient(api_key=""),
            matrix_provider=_HealthyMatrix(),
            config=_FAST_CONFIG,
        )
        report = orch.run(_fleet())

        assert report.llm_assisted is False
        assert report.grade is not None, "the grade is deterministic; it must survive a missing key"
        assert report.grade.overall.score is not None
        assert report.fleet_metrics.total_routes == 2

    def test_every_applicable_tool_runs(self) -> None:
        """Selection was the LLM's only job here. Without it, run them all —
        that is a more complete analysis, not a degraded one."""
        orch = AnalysisOrchestrator(
            client=LLMClient(api_key=""),
            matrix_provider=_HealthyMatrix(),
            config=_FAST_CONFIG,
        )
        report = orch.run(_fleet())

        assert len(report.analyses_run) >= 5, f"only ran {report.analyses_run}"
        assert "route_benchmark" in report.analyses_run

    def test_findings_are_produced(self) -> None:
        orch = AnalysisOrchestrator(
            client=LLMClient(api_key=""),
            matrix_provider=_HealthyMatrix(),
            config=_FAST_CONFIG,
        )
        assert orch.run(_fleet()).findings

    def test_an_llm_backed_run_is_still_marked_assisted(self) -> None:
        """The control: the flag tracks the LLM, not the code path generally."""
        from unittest.mock import MagicMock

        from routebench.agent.client import LLMResponse

        client = MagicMock(spec=LLMClient)
        client.available = True
        client._model = "stub"
        client.generate.return_value = LLMResponse(
            content=[
                {
                    "type": "tool_use",
                    "id": "1",
                    "name": "analysis_complete",
                    "input": {"summary": "d"},
                }
            ],
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
            model="stub",
        )
        orch = AnalysisOrchestrator(
            client=client, matrix_provider=_HealthyMatrix(), config=_FAST_CONFIG
        )
        assert orch.run(_fleet()).llm_assisted is True


class TestDeterministicProse:
    """Templated prose restates structured data and cannot invent a number."""

    def test_fills_a_slot_without_a_client(self) -> None:
        """Reachable as a plain function — the no-LLM path has no client to
        construct a verifier with and should not have to invent one."""
        slot = ProseSlot(
            slot_id="executive_summary",
            slot_type="executive_summary",
            prompt_template="writer_executive_summary",
            input_data={
                "fleet_metrics": {
                    "total_routes": 3,
                    "total_stops": 21,
                    "total_distance_miles": 142.5,
                }
            },
            word_budget=100,
        )
        text = deterministic_prose(slot)

        assert "3" in text and "21" in text and "142.5" in text
        assert text.strip()


class TestEndToEndWithoutAKey:
    def test_upload_succeeds_and_produces_an_analysis(self) -> None:
        app = create_app(
            Settings(
                anthropic_api_key="",
                osrm_host="http://127.0.0.1:59999",
                storage_path=tempfile.mkdtemp(),
            )
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            status = _run_to_completion(client)
            assert status["state"] == "succeeded", f"got {status}"

            analysis = client.get(f"/sessions/{status['_session_id']}/analysis.json")
            assert analysis.status_code == 200
            body = analysis.json()
            assert body["llm_assisted"] is False
            assert body["fleet_metrics"]["total_routes"] == 2
            assert body["analyses_run"], "no analyzers ran"

            assert client.get(f"/sessions/{status['_session_id']}/report.html").status_code == 200


class TestBudgetExhaustionDegrades:
    """A spent budget used to 503 every upload for the rest of the UTC day —
    taking the whole site down over a cap on the one optional part."""

    def _app_with_spent_budget(self) -> Any:
        app = create_app(
            Settings(
                anthropic_api_key="test-key",
                osrm_host="http://127.0.0.1:59999",
                storage_path=tempfile.mkdtemp(),
            )
        )
        app.state.budget_tracker.is_exceeded = AsyncMock(return_value=True)
        app.state.deps.budget_tracker.is_exceeded = AsyncMock(return_value=True)
        return app

    def test_upload_is_accepted_not_rejected(self) -> None:
        with TestClient(self._app_with_spent_budget(), raise_server_exceptions=False) as client:
            resp = client.post(
                "/sessions",
                files={"file": ("routes.csv", _CSV, "text/csv")},
                data={"config": _FAST},
            )
        assert resp.status_code == 202, (
            f"expected the upload to be accepted and degraded, got {resp.status_code} "
            f"— a spent budget must not take the service offline"
        )

    def test_the_analysis_completes_without_the_llm(self) -> None:
        with TestClient(self._app_with_spent_budget(), raise_server_exceptions=False) as client:
            status = _run_to_completion(client)
            assert status["state"] == "succeeded", f"got {status}"

            body = client.get(f"/sessions/{status['_session_id']}/analysis.json").json()
            assert body["llm_assisted"] is False, "budget was spent; the LLM should be withheld"
            assert body["analyses_run"]


class TestReportExplainsTheMode:
    def test_footer_does_not_claim_prose_failed_verification(self) -> None:
        """The pre-existing footer said fallbacks meant generated prose 'failed
        verification twice'. With no LLM nothing was generated, so that wording
        would be a lie — the report has to say which case it is."""
        from routebench.report.document import ReportDocument

        orch = AnalysisOrchestrator(
            client=LLMClient(api_key=""),
            matrix_provider=_HealthyMatrix(),
            config=_FAST_CONFIG,
        )
        report = orch.run(_fleet())
        doc = ReportDocument(report)
        slots = doc.identify_prose_slots()

        from routebench.agent.verifier import VerificationResult

        verification = {
            s.slot_id: VerificationResult(
                slot_id=s.slot_id, passed=False, issues=[], status="fallback"
            )
            for s in slots
        }
        html = doc.render(
            {s.slot_id: deterministic_prose(s) for s in slots}, verification=verification
        )

        assert "without a language model" in html
        assert "failed verification twice" not in html
