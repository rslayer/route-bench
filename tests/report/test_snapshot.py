"""Snapshot test for HTML report rendering.

Verifies key structural elements are present and consistent.
Not a pixel-perfect snapshot — checks for critical sections, CSS, and data.
"""

from __future__ import annotations

from datetime import UTC, datetime

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

AnalysisReport.model_rebuild()


def _ts() -> datetime:
    return datetime(2025, 1, 15, 8, 0, 0, tzinfo=UTC)


def _make_fixture_report() -> AnalysisReport:
    """Build a deterministic report for snapshot testing."""
    stops = [
        Stop(
            route_id="R-001",
            stop_sequence=i,
            latitude=32.83 + i * 0.01,
            longitude=-96.77,
            service_time_minutes=5.0,
            demand_units=10.0,
        )
        for i in range(1, 6)
    ]
    route = Route(
        route_id="R-001",
        stops=stops,
        depot_lat=32.825,
        depot_lon=-96.775,
        planned_start_time=_ts(),
        vehicle_capacity_units=100.0,
    )
    fleet = Fleet(routes=[route], upload_id="snap", uploaded_at=_ts())

    findings = [
        Finding(
            category="sequencing",
            severity="high",
            confidence=0.92,
            title="Route R-001 has suboptimal sequencing",
            evidence=[
                FindingEvidence(
                    metric_name="sequencing_index",
                    actual_value=1.45,
                    comparison_value=1.30,
                    comparison_type="threshold",
                    unit="ratio",
                ),
            ],
            references=FindingReference(route_ids=["R-001"]),
            hypothesis="Geographic crossings suggest suboptimal ordering",
            suggested_investigation="Review stop ordering",
        ),
        Finding(
            category="time_pressure",
            severity="medium",
            confidence=0.85,
            title="Route R-001 has significant idle time",
            evidence=[
                FindingEvidence(
                    metric_name="idle_time_hours",
                    actual_value=1.2,
                    comparison_value=0.5,
                    comparison_type="threshold",
                    unit="hours",
                ),
            ],
            references=FindingReference(route_ids=["R-001"]),
            hypothesis="Time window constraints cause idle periods",
            suggested_investigation="Check time window clustering",
        ),
    ]

    route_metrics = {
        "R-001": RouteMetrics(
            route_id="R-001",
            total_distance_miles=45.0,
            total_time_hours=8.0,
            drive_time_hours=5.0,
            service_time_hours=2.0,
            idle_time_hours=1.0,
            stop_count=5,
            stops_per_hour=6.0,
            sequencing_index=1.45,
            capacity_utilization={"units": 0.50},
        ),
    }
    fleet_metrics = FleetMetrics(
        total_routes=1,
        total_stops=5,
        total_distance_miles=45.0,
        total_time_hours=8.0,
        median_sequencing_index=1.45,
        routes_over_shift_cap=0,
        avg_capacity_utilization={"units": 0.50},
    )

    return AnalysisReport(
        fleet=fleet,
        fleet_metrics=fleet_metrics,
        route_metrics=route_metrics,
        findings=findings,
        analyses_run=["analyze_sequencing", "analyze_time_pressure"],
        analyses_skipped=[],
        metadata={"test": True},
    )


class TestHTMLSnapshot:
    """Verify HTML structure remains consistent."""

    def test_html_has_required_sections(self) -> None:
        report = _make_fixture_report()
        doc = ReportDocument(report)
        prose = {
            "executive_summary": "This fleet has one route with suboptimal sequencing.",
            "fleet_overview_narrative": "Fleet of 1 route covering 5 stops.",
            "investigation_priorities": "1. Address sequencing issues on R-001.",
        }
        html = doc.render(prose)

        # Structural elements
        assert "<style>" in html
        assert "Fleet Overview" in html
        assert "Per-Route Findings" in html
        assert "Investigation Priorities" in html
        assert "Methodology" in html

    def test_html_contains_metric_values(self) -> None:
        report = _make_fixture_report()
        doc = ReportDocument(report)
        prose = {}
        html = doc.render(prose)

        assert "45.0" in html or "45.00" in html
        assert "R-001" in html

    def test_html_finding_cards_present(self) -> None:
        report = _make_fixture_report()
        doc = ReportDocument(report)
        prose = {}
        html = doc.render(prose)

        assert "severity-high" in html
        assert "severity-medium" in html
        assert "suboptimal sequencing" in html

    def test_html_css_inlined(self) -> None:
        report = _make_fixture_report()
        doc = ReportDocument(report)
        html = doc.render({})

        assert "<style>" in html
        assert "--color-accent" in html
        assert "finding-card" in html

    def test_html_is_self_contained(self) -> None:
        """No external stylesheet links."""
        report = _make_fixture_report()
        doc = ReportDocument(report)
        html = doc.render({})

        assert '<link rel="stylesheet"' not in html
