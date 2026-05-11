"""Tests for report rendering — HTML + PDF."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from routebench.core.findings import (
    AnalysisReport,
    Finding,
    FindingEvidence,
    FindingReference,
    FleetMetrics,
    RouteMetrics,
)
from routebench.core.schemas import Fleet, Route, Stop
from routebench.report.document import ReportDocument
from routebench.report.pdf import render_pdf

AnalysisReport.model_rebuild()


def _ts(hour: int = 8) -> datetime:
    return datetime(2025, 1, 15, hour, 0, 0, tzinfo=timezone.utc)


def _make_stop(route_id: str, seq: int) -> Stop:
    return Stop(
        route_id=route_id,
        stop_sequence=seq,
        latitude=32.83 + seq * 0.01,
        longitude=-96.77 + seq * 0.005,
        service_time_minutes=5.0,
    )


def _make_route(route_id: str, n_stops: int = 5) -> Route:
    return Route(
        route_id=route_id,
        stops=[_make_stop(route_id, i) for i in range(1, n_stops + 1)],
        depot_lat=32.825,
        depot_lon=-96.775,
        planned_start_time=_ts(),
    )


def _make_fleet(n_routes: int = 3) -> Fleet:
    return Fleet(
        routes=[
            _make_route(f"R-{i:03d}", n_stops=5)
            for i in range(1, n_routes + 1)
        ],
        upload_id="test-session",
        uploaded_at=_ts(),
    )


def _make_finding(
    route_id: str, severity: str = "high", gap: float = 25.0,
) -> Finding:
    return Finding(
        category="sequencing",
        severity=severity,  # type: ignore[arg-type]
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


def _make_report() -> AnalysisReport:
    fleet = _make_fleet(n_routes=3)
    findings = [
        _make_finding("R-001", severity="critical", gap=35.0),
        _make_finding("R-002", severity="high", gap=25.0),
        _make_finding("R-003", severity="low", gap=8.0),
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
            time_window_violations=1,
            shift_overrun_minutes=15.0,
        )
        for r in fleet.routes
    }
    fleet_metrics = FleetMetrics(
        total_routes=3,
        total_stops=15,
        total_distance_miles=135.0,
        total_time_hours=24.0,
        routes_over_shift_cap=1,
    )
    return AnalysisReport(
        fleet=fleet,
        fleet_metrics=fleet_metrics,
        route_metrics=route_metrics,
        findings=findings,
        analyses_run=["analyze_sequencing", "analyze_time_pressure"],
        analyses_skipped=[("analyze_territory", "single route fleet")],
        metadata={"session_id": "test-001"},
    )


def _make_prose(report: AnalysisReport) -> dict[str, str]:
    """Build hand-written prose for all expected slots."""
    prose: dict[str, str] = {
        "executive_summary": (
            "Analysis of 3 routes serving 15 stops reveals "
            "sequencing inefficiencies in routes R-001 and R-002."
        ),
        "fleet_overview_narrative": (
            "The fleet covers 135.0 miles across 3 routes, "
            "averaging 8.0 hours per route."
        ),
        "investigation_priorities": (
            "1. Resequence R-001 (35% gap). "
            "2. Resequence R-002 (25% gap)."
        ),
    }
    for f in report.findings:
        prose[f"finding_{f.finding_id}"] = (
            f"Finding {f.finding_id}: {f.title}"
        )
    return prose


class TestReportDocument:
    """Tests for HTML rendering."""

    def test_render_produces_html(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        prose = _make_prose(report)
        html = doc.render(prose)

        assert "<!DOCTYPE html>" in html
        assert "RouteBench Analysis Report" in html

    def test_key_sections_present(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        prose = _make_prose(report)
        html = doc.render(prose)

        assert "Executive Summary" in html
        assert "Fleet Overview" in html
        assert "Per-Route Findings" in html
        assert "Investigation Priorities" in html
        assert "Methodology" in html
        assert "Caveats" in html

    def test_key_numbers_appear(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        prose = _make_prose(report)
        html = doc.render(prose)

        assert "135.0" in html  # total distance
        assert "24.0" in html  # total time
        assert "15" in html  # total stops
        assert "3 routes" in html

    def test_findings_appear(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        prose = _make_prose(report)
        html = doc.render(prose)

        assert "R-001" in html
        assert "R-002" in html
        assert "suboptimal sequencing" in html

    def test_prose_slots_included(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        prose = _make_prose(report)
        html = doc.render(prose)

        assert "sequencing inefficiencies" in html
        assert "135.0 miles" in html

    def test_css_inlined(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        prose = _make_prose(report)
        html = doc.render(prose)

        assert "<style>" in html
        assert "--color-accent" in html

    def test_identify_prose_slots(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        slots = doc.identify_prose_slots()

        slot_types = [s.slot_type for s in slots]
        assert "executive_summary" in slot_types
        assert "fleet_overview_narrative" in slot_types
        assert "investigation_priorities" in slot_types

    def test_no_jinja_errors_with_empty_prose(self) -> None:
        """Rendering with empty prose dict should not raise."""
        report = _make_report()
        doc = ReportDocument(report)
        html = doc.render({})

        assert "<!DOCTYPE html>" in html
        assert "RouteBench Analysis Report" in html


class TestPDFRendering:
    """Tests for PDF rendering."""

    def test_pdf_renders_without_error(self) -> None:
        report = _make_report()
        doc = ReportDocument(report)
        prose = _make_prose(report)
        html = doc.render(prose)

        pdf_bytes = render_pdf(html)
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:5] == b"%PDF-"
