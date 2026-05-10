"""Tests for agent layer — orchestrator, writer, verifier (mocked LLM)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from routebench.agent.client import LLMClient, LLMResponse
from routebench.agent.tool_specs import build_tool_specs
from routebench.agent.verifier import VerificationResult, Verifier, verify_slot
from routebench.agent.writer import ReportWriter
from routebench.core.findings import (
    AnalysisReport,
    Finding,
    FindingEvidence,
    FindingReference,
    FleetMetrics,
    RouteMetrics,
)
from routebench.core.schemas import Fleet, Route, Stop

# Rebuild AnalysisReport to resolve forward ref to Fleet
AnalysisReport.model_rebuild()
from routebench.report.prose_slots import ProseSlot, identify_prose_slots


def _ts(hour: int = 8) -> datetime:
    return datetime(2025, 1, 15, hour, 0, 0, tzinfo=timezone.utc)


def _make_stop(route_id: str, seq: int) -> Stop:
    return Stop(
        route_id=route_id,
        stop_sequence=seq,
        latitude=32.83 + seq * 0.01,
        longitude=-96.77,
        service_time_minutes=5.0,
    )


def _make_route(route_id: str, n_stops: int = 3) -> Route:
    return Route(
        route_id=route_id,
        stops=[_make_stop(route_id, i) for i in range(1, n_stops + 1)],
        depot_lat=32.825,
        depot_lon=-96.775,
        planned_start_time=_ts(),
    )


def _make_fleet(n_routes: int = 3) -> Fleet:
    return Fleet(
        routes=[_make_route(f"R-{i:03d}", n_stops=5) for i in range(1, n_routes + 1)],
        upload_id="test",
        uploaded_at=_ts(),
    )


def _make_finding(route_id: str = "R-001", gap: float = 25.0) -> Finding:
    return Finding(
        category="sequencing",
        severity="high",
        confidence=0.95,
        title=f"Route {route_id} has suboptimal sequencing",
        evidence=[
            FindingEvidence(
                metric_name="distance_gap_pct",
                actual_value=gap,
                comparison_value=0.0,
                comparison_type="optimal",
                unit="%",
            ),
        ],
        references=FindingReference(route_ids=[route_id]),
        hypothesis="Route stops are not optimally ordered",
        suggested_investigation="Resequence using nearest-neighbor",
    )


def _make_report(
    fleet: Fleet | None = None,
    n_findings: int = 3,
) -> AnalysisReport:
    fleet = fleet or _make_fleet()
    findings = [
        _make_finding(f"R-{i:03d}", gap=25.0 - i * 5)
        for i in range(1, n_findings + 1)
    ]
    route_metrics = {
        r.route_id: RouteMetrics(
            route_id=r.route_id,
            total_distance_miles=45.0,
            total_time_hours=8.0,
            drive_time_hours=5.0,
            service_time_hours=2.0,
            idle_time_hours=1.0,
            stop_count=len(r.stops),
            stops_per_hour=6.0,
        )
        for r in fleet.routes
    }
    fleet_metrics = FleetMetrics(
        total_routes=len(fleet.routes),
        total_stops=sum(len(r.stops) for r in fleet.routes),
        total_distance_miles=135.0,
        total_time_hours=24.0,
        routes_over_shift_cap=0,
    )
    return AnalysisReport(
        fleet=fleet,
        fleet_metrics=fleet_metrics,
        route_metrics=route_metrics,
        findings=findings,
        analyses_run=["analyze_sequencing"],
        analyses_skipped=[],
        metadata={"test": True},
    )


def _mock_llm_response(
    text: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    stop_reason: str = "end_turn",
) -> LLMResponse:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    if tool_calls:
        content.extend(tool_calls)
    return LLMResponse(
        content=content,
        stop_reason=stop_reason,
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-7",
    )


class TestToolSpecs:
    """Tests for tool_specs.py."""

    def test_builds_specs_from_registry(self) -> None:
        import routebench.analysis  # noqa: F401

        specs = build_tool_specs()
        names = {s["name"] for s in specs}
        assert "analyze_sequencing" in names
        assert "route_benchmark" in names
        assert "analysis_complete" in names

    def test_done_tool_included(self) -> None:
        specs = build_tool_specs([])
        assert any(s["name"] == "analysis_complete" for s in specs)


class TestOrchestrator:
    """Tests for AnalysisOrchestrator with mocked LLM."""

    def test_orchestrator_skips_inapplicable_tools(self) -> None:
        """Single-route fleet: territory/dispatch tools should be skipped."""
        from routebench.agent.orchestrator import AnalysisOrchestrator

        fleet = _make_fleet(n_routes=1)

        mock_client = MagicMock(spec=LLMClient)
        mock_client._model = "claude-sonnet-4-7"
        # LLM signals completion on first call
        mock_client.generate.return_value = _mock_llm_response(
            tool_calls=[{
                "type": "tool_use",
                "id": "call_1",
                "name": "analysis_complete",
                "input": {"summary": "Single route analyzed"},
            }],
        )

        mock_provider = MagicMock()
        mock_provider.name = "mock"

        # Mock compute_scorecard to avoid needing real matrix provider
        with patch("routebench.agent.orchestrator.compute_scorecard") as mock_sc:
            mock_sc.return_value = (
                FleetMetrics(
                    total_routes=1, total_stops=5,
                    total_distance_miles=20.0, total_time_hours=4.0,
                    routes_over_shift_cap=0,
                ),
                {
                    "R-001": RouteMetrics(
                        route_id="R-001",
                        total_distance_miles=20.0,
                        total_time_hours=4.0,
                        drive_time_hours=2.5,
                        service_time_hours=1.0,
                        idle_time_hours=0.5,
                        stop_count=5,
                        stops_per_hour=5.0,
                    ),
                },
            )

            orch = AnalysisOrchestrator(
                client=mock_client,
                matrix_provider=mock_provider,
            )
            report = orch.run(fleet)

        assert report.fleet_metrics.total_routes == 1
        # Territory and dispatch should be skipped for single-route fleets
        skipped_names = [name for name, _ in report.analyses_skipped]
        assert "analyze_territory" in skipped_names
        assert "analyze_dispatch" in skipped_names

    def test_orchestrator_executes_tool_calls(self) -> None:
        """LLM invokes a tool, then signals completion."""
        from routebench.agent.orchestrator import AnalysisOrchestrator

        fleet = _make_fleet(n_routes=3)

        mock_client = MagicMock(spec=LLMClient)
        mock_client._model = "claude-sonnet-4-7"
        # First call: LLM requests analyze_sequencing
        # Second call: LLM signals done
        mock_client.generate.side_effect = [
            _mock_llm_response(
                tool_calls=[{
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "analyze_sequencing",
                    "input": {"tool_name": "analyze_sequencing"},
                }],
            ),
            _mock_llm_response(
                tool_calls=[{
                    "type": "tool_use",
                    "id": "call_2",
                    "name": "analysis_complete",
                    "input": {"summary": "Sequencing analyzed"},
                }],
            ),
        ]

        mock_provider = MagicMock()
        mock_provider.name = "mock"

        with patch("routebench.agent.orchestrator.compute_scorecard") as mock_sc:
            mock_sc.return_value = (
                FleetMetrics(
                    total_routes=3, total_stops=15,
                    total_distance_miles=60.0, total_time_hours=12.0,
                    routes_over_shift_cap=0,
                ),
                {
                    f"R-{i:03d}": RouteMetrics(
                        route_id=f"R-{i:03d}",
                        total_distance_miles=20.0,
                        total_time_hours=4.0,
                        drive_time_hours=2.5,
                        service_time_hours=1.0,
                        idle_time_hours=0.5,
                        stop_count=5,
                        stops_per_hour=5.0,
                    )
                    for i in range(1, 4)
                },
            )

            orch = AnalysisOrchestrator(
                client=mock_client,
                matrix_provider=mock_provider,
            )
            report = orch.run(fleet)

        assert "analyze_sequencing" in report.analyses_run
        assert mock_client.generate.call_count == 2


class TestProseSlots:
    """Tests for prose slot identification."""

    def test_identifies_correct_slots(self) -> None:
        report = _make_report(n_findings=3)
        slots = identify_prose_slots(report)

        slot_types = [s.slot_type for s in slots]
        assert "executive_summary" in slot_types
        assert "fleet_overview_narrative" in slot_types
        assert "investigation_priorities" in slot_types

        # 3 findings => 3 finding_explanation slots
        explanation_slots = [s for s in slots if s.slot_type == "finding_explanation"]
        assert len(explanation_slots) == 3

    def test_cross_fleet_synthesis_requires_multiple(self) -> None:
        """Cross-fleet synthesis only if >= 2 cross-fleet findings."""
        report = _make_report(n_findings=1)
        slots = identify_prose_slots(report)
        assert not any(s.slot_type == "cross_fleet_synthesis" for s in slots)

    def test_finding_explanations_capped_at_15(self) -> None:
        """Max 15 finding explanation slots."""
        report = _make_report(n_findings=20)
        slots = identify_prose_slots(report)
        explanation_slots = [s for s in slots if s.slot_type == "finding_explanation"]
        assert len(explanation_slots) == 15


class TestWriter:
    """Tests for ReportWriter with mocked LLM."""

    def test_fills_slots(self) -> None:
        mock_client = MagicMock(spec=LLMClient)
        mock_client.generate.return_value = _mock_llm_response(
            text="This is generated prose.",
        )

        writer = ReportWriter(client=mock_client, max_workers=1)
        report = _make_report(n_findings=1)
        slots = identify_prose_slots(report)

        filled = writer.fill_slots(slots)
        assert len(filled) == len(slots)
        for slot_id, prose in filled.items():
            assert isinstance(prose, str)
            assert len(prose) > 0


class TestVerifier:
    """Tests for the verifier."""

    def test_catches_fabricated_number(self) -> None:
        """Prose with a number not in source data should fail."""
        slot = ProseSlot(
            slot_id="test_slot",
            slot_type="finding_explanation",
            prompt_template="writer_finding_explanation",
            input_data={
                "finding": {
                    "finding_id": "abc12345",
                    "category": "sequencing",
                    "severity": "high",
                    "evidence": [
                        {"metric_name": "distance_gap_pct", "actual_value": 25.3}
                    ],
                    "references": {"route_ids": ["R-001"]},
                },
            },
            word_budget=150,
            required_references=["abc12345"],
        )

        # Prose contains 42% which is fabricated
        bad_prose = "Route R-001 shows a 42% distance gap (finding abc12345)."
        result = verify_slot(bad_prose, slot)
        assert not result.passed
        assert any("42" in issue for issue in result.issues)

    def test_passes_valid_prose(self) -> None:
        """Prose with only source numbers should pass."""
        slot = ProseSlot(
            slot_id="test_slot",
            slot_type="finding_explanation",
            prompt_template="writer_finding_explanation",
            input_data={
                "finding": {
                    "finding_id": "abc12345",
                    "category": "sequencing",
                    "severity": "high",
                    "evidence": [
                        {"metric_name": "distance_gap_pct", "actual_value": 25.3}
                    ],
                    "references": {"route_ids": ["R-001"]},
                },
            },
            word_budget=150,
            required_references=["abc12345"],
        )

        good_prose = "Route R-001 shows a 25.3% distance gap (finding abc12345)."
        result = verify_slot(good_prose, slot)
        assert result.passed

    def test_catches_missing_required_reference(self) -> None:
        """Prose missing a required finding ID should fail."""
        slot = ProseSlot(
            slot_id="test_slot",
            slot_type="executive_summary",
            prompt_template="writer_executive_summary",
            input_data={"fleet_metrics": {"total_routes": 3}},
            word_budget=200,
            required_references=["abc12345"],
        )

        prose = "This fleet has 3 routes."
        result = verify_slot(prose, slot)
        assert not result.passed
        assert any("abc12345" in issue for issue in result.issues)

    def test_catches_fabricated_route_id(self) -> None:
        """Prose with a route ID not in source data should fail."""
        slot = ProseSlot(
            slot_id="test_slot",
            slot_type="finding_explanation",
            prompt_template="writer_finding_explanation",
            input_data={
                "finding": {
                    "references": {"route_ids": ["R-001"]},
                },
            },
            word_budget=150,
        )

        prose = "Route R-999 has issues."
        result = verify_slot(prose, slot)
        assert not result.passed
        assert any("R-999" in issue for issue in result.issues)

    def test_verify_and_regenerate_with_fallback(self) -> None:
        """Verifier falls back to deterministic template on repeated failure."""
        slot = ProseSlot(
            slot_id="test_slot",
            slot_type="executive_summary",
            prompt_template="writer_executive_summary",
            input_data={
                "fleet_metrics": {
                    "total_routes": 3,
                    "total_stops": 15,
                    "total_distance_miles": 45.2,
                },
            },
            word_budget=200,
            required_references=["abc12345"],
        )

        # Both original and retry prose have fabricated data
        bad_prose = "Fleet has 99 routes."

        def bad_writer(s: ProseSlot) -> str:
            return "Fleet has 88 routes."

        verifier = Verifier(client=None, use_llm_judge=False)
        final_prose, results = verifier.verify_and_regenerate(
            {"test_slot": bad_prose}, [slot], bad_writer,
        )

        # Should have used deterministic fallback
        assert "test_slot" in final_prose
        assert results["test_slot"].passed
