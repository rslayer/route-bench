"""End-to-end tests: full pipeline with mocked LLM."""

from __future__ import annotations

from datetime import time
from typing import Any
from unittest.mock import MagicMock, patch

from routebench.agent.client import LLMClient, LLMResponse
from routebench.agent.orchestrator import AnalysisOrchestrator
from routebench.agent.writer import ReportWriter
from routebench.core.config import AnalysisConfig
from routebench.core.schemas import Fleet, Route, Stop
from routebench.report.document import ReportDocument
from tests.conftest import make_ts, mock_matrix_realistic


def _mock_llm_response(
    text: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> LLMResponse:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    if tool_calls:
        content.extend(tool_calls)
    return LLMResponse(
        content=content,
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-7",
    )


def _build_test_fleet() -> Fleet:
    """Build a fleet with 3 routes, 5 stops each, with time windows."""
    routes = []
    for i in range(1, 4):
        stops = []
        for j in range(1, 6):
            stops.append(
                Stop(
                    route_id=f"R-{i:03d}",
                    stop_sequence=j,
                    latitude=32.83 + j * 0.01,
                    longitude=-96.77 + i * 0.01,
                    service_time_minutes=5.0,
                    demand_units=10.0,
                    time_window_start=time(8 + j, 0),
                    time_window_end=time(8 + j, 30),
                )
            )
        routes.append(
            Route(
                route_id=f"R-{i:03d}",
                stops=stops,
                depot_lat=32.825,
                depot_lon=-96.775,
                planned_start_time=make_ts(8),
                vehicle_capacity_units=100.0,
            )
        )
    return Fleet(routes=routes, upload_id="e2e-test", uploaded_at=make_ts())


class TestE2EPipeline:
    """Full pipeline tests: orchestrate → write → verify → render."""

    def test_full_pipeline_with_mocked_llm(self) -> None:
        """Run orchestrator → writer → verifier → render with mocked LLM."""
        fleet = _build_test_fleet()

        # Mock LLM client
        mock_client = MagicMock(spec=LLMClient)
        mock_client._model = "claude-sonnet-4-7"

        # Orchestrator: run sequencing, then done
        mock_client.generate.side_effect = [
            _mock_llm_response(
                tool_calls=[
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "analyze_sequencing",
                        "input": {"tool_name": "analyze_sequencing"},
                    }
                ]
            ),
            _mock_llm_response(
                tool_calls=[
                    {
                        "type": "tool_use",
                        "id": "call_2",
                        "name": "analyze_time_pressure",
                        "input": {"tool_name": "analyze_time_pressure"},
                    }
                ]
            ),
            _mock_llm_response(
                tool_calls=[
                    {
                        "type": "tool_use",
                        "id": "call_3",
                        "name": "analysis_complete",
                        "input": {"summary": "Analysis done"},
                    }
                ]
            ),
        ]

        # Mock matrix provider returns realistic matrices
        mock_provider = MagicMock()
        mock_provider.name = "mock"
        mock_provider.get_matrix.side_effect = lambda origins, dests, *args, **kwargs: (
            mock_matrix_realistic(len(origins))
        )

        with patch("routebench.agent.orchestrator.get_route_matrix") as mock_grm:
            mock_grm.side_effect = lambda route, *args, **kwargs: mock_matrix_realistic(
                len(route.stops) + 1
            )

            orch = AnalysisOrchestrator(
                client=mock_client,
                matrix_provider=mock_provider,
            )
            report = orch.run(fleet)

        assert report.fleet_metrics.total_routes == 3
        assert report.fleet_metrics.total_stops == 15
        assert len(report.analyses_run) >= 1

        # Writer: mock LLM prose generation
        writer_client = MagicMock(spec=LLMClient)
        writer_client._model = "claude-sonnet-4-7"
        writer_client.generate.return_value = _mock_llm_response(
            text="Fleet of 3 routes covering 15 stops. R-001 finding abc.",
        )

        writer = ReportWriter(client=writer_client)
        doc = ReportDocument(report)
        slots = doc.identify_prose_slots()
        assert len(slots) >= 3  # exec summary + fleet overview + priorities

        prose = writer.fill_slots(slots)
        assert len(prose) >= 3

        # Render HTML
        html = doc.render(prose)
        assert "<!DOCTYPE html>" in html or "<html" in html
        assert "Fleet Overview" in html
        assert "Per-Route Findings" in html

    def test_scorecard_populates_all_metrics(self) -> None:
        """Scorecard computes distance, time, density, utilization, sequencing."""
        from routebench.analysis.scoring import compute_scorecard

        fleet = _build_test_fleet()
        mock_provider = MagicMock()
        mock_provider.get_matrix.side_effect = lambda origins, dests, *args, **kwargs: (
            mock_matrix_realistic(len(origins))
        )

        config = AnalysisConfig()
        fleet_metrics, route_metrics = compute_scorecard(
            fleet,
            mock_provider,
            config,
        )

        assert fleet_metrics.total_routes == 3
        assert fleet_metrics.total_stops == 15
        assert fleet_metrics.total_distance_miles > 0
        assert fleet_metrics.total_time_hours > 0

        for rm in route_metrics.values():
            assert rm.total_distance_miles > 0
            assert rm.drive_time_hours > 0
            assert rm.service_time_hours > 0
