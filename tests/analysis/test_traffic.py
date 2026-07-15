"""Regression tests: what a traffic profile does, and does not, change.

The contract is narrow. A profile must move the clock — drive time, time-window
feasibility, shift overrun, compliance findings — and must leave geometry alone.
Distances and the sequencing index are computed from distances and must come out
byte-identical with and without a profile.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time

import pytest

from routebench.analysis.diagnosis.compliance import ComplianceAnalysis
from routebench.analysis.scoring import compute_scorecard
from routebench.analysis.scoring.distance import get_route_matrix
from routebench.analysis.scoring.time import compute_departure_schedule, compute_time_metrics
from routebench.core.config import (
    URBAN_US_PROFILE,
    AnalysisConfig,
    TrafficProfile,
    WorkRules,
)
from routebench.core.findings import AnalysisReport, Finding
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.traffic import TrafficAdjustedProvider
from routebench.report.document import ReportDocument

AnalysisReport.model_rebuild()

# 10-minute legs, 5km apart.
LEG_SECONDS = 600.0
LEG_METERS = 5000.0


class FlatProvider:
    """Uniform free-flow matrix, so band effects are easy to reason about."""

    name: str = "flat"

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        n = len(origins)
        return MatrixResult(
            durations_seconds=[[LEG_SECONDS] * n for _ in range(n)],
            distances_meters=[[LEG_METERS] * n for _ in range(n)],
            provider="flat",
            cached=False,
        )


def _fleet(
    start_hour: int = 7,
    start_minute: int = 30,
    n_stops: int = 4,
    window_end: time | None = time(8, 30),
    planned_arrival: datetime | None = None,
) -> Fleet:
    """A morning route whose later stops are close to their window edge.

    Departing 07:30 puts every leg inside urban_us's 07:00-09:00 peak band.
    """
    stops = [
        Stop(
            route_id="R1",
            stop_sequence=i,
            latitude=32.80 + 0.01 * i,
            longitude=-96.80,
            service_time_minutes=5.0,
            time_window_end=window_end,
            planned_arrival_time=planned_arrival,
        )
        for i in range(1, n_stops + 1)
    ]
    route = Route(
        route_id="R1",
        stops=stops,
        depot_lat=32.79,
        depot_lon=-96.80,
        planned_start_time=datetime(2025, 1, 15, start_hour, start_minute, tzinfo=UTC),
    )
    return Fleet(routes=[route], upload_id="t", uploaded_at=datetime(2025, 1, 15, tzinfo=UTC))


def _plain(html: str) -> str:
    """Strip tags and collapse whitespace.

    Assertions target what a reader sees, so re-wrapping a template paragraph
    must not fail a test.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _render(fleet: Fleet, profile: TrafficProfile | None) -> str:
    """Render a real report, mirroring how the orchestrator stamps metadata."""
    provider = FlatProvider()
    wrapped = TrafficAdjustedProvider(provider, profile) if profile else provider
    config = AnalysisConfig(traffic=profile) if profile else AnalysisConfig()
    fleet_metrics, route_metrics = compute_scorecard(fleet, wrapped, config)  # type: ignore[arg-type]

    report = AnalysisReport(
        fleet=fleet,
        fleet_metrics=fleet_metrics,
        route_metrics=route_metrics,
        findings=[],
        analyses_run=[],
        analyses_skipped=[],
        metadata={
            "orchestrator_model": "test",
            "traffic_profile": {
                "active": config.traffic.is_active,
                "profile_hash": config.traffic.profile_hash(),
                "default_factor": config.traffic.default_factor,
                "bands": [
                    {
                        "start": b.start.strftime("%H:%M"),
                        "end": b.end.strftime("%H:%M"),
                        "speed_factor": b.speed_factor,
                    }
                    for b in config.traffic.bands
                ],
            },
        },
    )
    return ReportDocument(report).render({})


def _scorecard(fleet: Fleet, profile: TrafficProfile | None):
    provider = FlatProvider()
    wrapped = TrafficAdjustedProvider(provider, profile) if profile else provider
    config = AnalysisConfig(traffic=profile) if profile else AnalysisConfig()
    return compute_scorecard(fleet, wrapped, config)  # type: ignore[arg-type]


def _compliance_findings(fleet: Fleet, profile: TrafficProfile | None) -> list[Finding]:
    provider = FlatProvider()
    wrapped = TrafficAdjustedProvider(provider, profile) if profile else provider
    work_rules = WorkRules()
    matrix = get_route_matrix(fleet.routes[0], wrapped, work_rules)  # type: ignore[arg-type]
    return ComplianceAnalysis().run(
        fleet,
        matrices={"R1": matrix},
        work_rules=work_rules,
        traffic_profile=profile,
    )


class TestProfileMovesTheClock:
    """A profile must change time-denominated outputs."""

    def test_drive_time_grows_under_peak_band(self) -> None:
        _, free_flow = _scorecard(_fleet(), None)
        _, profiled = _scorecard(_fleet(), URBAN_US_PROFILE)

        # 0.75x speed across every leg -> 1/0.75 = 1.333x drive time
        assert profiled["R1"].drive_time_hours == pytest.approx(
            free_flow["R1"].drive_time_hours / 0.75
        )

    def test_time_window_feasibility_tightens(self) -> None:
        _, free_flow = _scorecard(_fleet(), None)
        _, profiled = _scorecard(_fleet(), URBAN_US_PROFILE)

        assert profiled["R1"].time_window_violations > free_flow["R1"].time_window_violations

    def test_total_time_grows(self) -> None:
        _, free_flow = _scorecard(_fleet(), None)
        _, profiled = _scorecard(_fleet(), URBAN_US_PROFILE)
        assert profiled["R1"].total_time_hours > free_flow["R1"].total_time_hours

    def test_offpeak_departure_is_unaffected_by_urban_us(self) -> None:
        """An 11:00 route sits outside every urban_us band, so nothing moves."""
        _, free_flow = _scorecard(_fleet(start_hour=11), None)
        _, profiled = _scorecard(_fleet(start_hour=11), URBAN_US_PROFILE)
        assert profiled["R1"].drive_time_hours == pytest.approx(free_flow["R1"].drive_time_hours)


class TestProfileLeavesGeometryAlone:
    """A profile must not touch anything derived from distances."""

    def test_distance_metrics_identical(self) -> None:
        free_flow_fleet, free_flow = _scorecard(_fleet(), None)
        profiled_fleet, profiled = _scorecard(_fleet(), URBAN_US_PROFILE)

        assert profiled["R1"].total_distance_miles == free_flow["R1"].total_distance_miles
        assert profiled_fleet.total_distance_miles == free_flow_fleet.total_distance_miles

    def test_sequencing_index_identical(self) -> None:
        _, free_flow = _scorecard(_fleet(), None)
        _, profiled = _scorecard(_fleet(), URBAN_US_PROFILE)
        assert profiled["R1"].sequencing_index == free_flow["R1"].sequencing_index

    def test_stop_count_identical(self) -> None:
        _, free_flow = _scorecard(_fleet(), None)
        _, profiled = _scorecard(_fleet(), URBAN_US_PROFILE)
        assert profiled["R1"].stop_count == free_flow["R1"].stop_count


class TestComplianceFindingsRespondToProfile:
    """The headline regression: same fleet, different compliance findings."""

    def test_profile_changes_compliance_findings(self) -> None:
        free_flow = _compliance_findings(_fleet(), None)
        profiled = _compliance_findings(_fleet(), URBAN_US_PROFILE)

        assert free_flow != profiled
        assert {f.finding_id for f in free_flow} != {f.finding_id for f in profiled}

    def test_profile_surfaces_more_violations(self) -> None:
        def violations(findings: list[Finding]) -> float:
            for f in findings:
                for e in f.evidence:
                    if e.metric_name == "time_window_violations":
                        return e.actual_value
            return 0.0

        assert violations(_compliance_findings(_fleet(), URBAN_US_PROFILE)) > violations(
            _compliance_findings(_fleet(), None)
        )

    def test_free_flow_findings_carry_the_caveat(self) -> None:
        findings = _compliance_findings(_fleet(), None)
        assert findings, "expected at least one compliance finding"
        assert all("lower bound" in f.hypothesis for f in findings)

    def test_profiled_findings_drop_the_hedge(self) -> None:
        findings = _compliance_findings(_fleet(), URBAN_US_PROFILE)
        assert findings, "expected at least one compliance finding"
        assert all("lower bound" not in f.hypothesis for f in findings)
        assert all("configured traffic profile" in f.hypothesis for f in findings)

    def test_findings_are_categorised_compliance(self) -> None:
        findings = _compliance_findings(_fleet(), URBAN_US_PROFILE)
        assert all(f.category == "compliance" for f in findings)


class TestOneClock:
    """Compliance grades on the matrix, not the static CSV column."""

    def test_violations_ignore_contradicting_planned_arrival_column(self) -> None:
        """A CSV claiming an on-time arrival cannot mask a late one."""
        optimistic = _fleet(planned_arrival=datetime(2025, 1, 15, 7, 35, tzinfo=UTC))
        _, metrics = _scorecard(optimistic, URBAN_US_PROFILE)
        assert metrics["R1"].time_window_violations > 0

    def test_violations_detected_without_planned_arrival_column(self) -> None:
        """The column is optional; its absence must not zero out violations."""
        no_column = _fleet(planned_arrival=None)
        _, metrics = _scorecard(no_column, URBAN_US_PROFILE)
        assert metrics["R1"].time_window_violations > 0


class TestDepartureSchedule:
    """The vector handed to the matrix layer."""

    def test_one_departure_per_matrix_index(self) -> None:
        fleet = _fleet(n_stops=4)
        route = fleet.routes[0]
        matrix = get_route_matrix(route, FlatProvider(), WorkRules())  # type: ignore[arg-type]
        schedule = compute_departure_schedule(route, matrix, WorkRules())
        assert len(schedule) == len(route.stops) + 1

    def test_depot_departs_after_pre_trip(self) -> None:
        fleet = _fleet(start_hour=7, start_minute=30)
        route = fleet.routes[0]
        matrix = get_route_matrix(route, FlatProvider(), WorkRules())  # type: ignore[arg-type]
        schedule = compute_departure_schedule(route, matrix, WorkRules(pre_trip_minutes=15.0))
        assert schedule[0].time() == time(7, 45)

    def test_schedule_is_monotonic(self) -> None:
        fleet = _fleet(n_stops=5, window_end=None)
        route = fleet.routes[0]
        matrix = get_route_matrix(route, FlatProvider(), WorkRules())  # type: ignore[arg-type]
        schedule = compute_departure_schedule(route, matrix, WorkRules())
        assert schedule == sorted(schedule)

    def test_empty_route_still_yields_depot_departure(self) -> None:
        route = Route(
            route_id="R0",
            stops=[],
            depot_lat=32.79,
            depot_lon=-96.80,
            planned_start_time=datetime(2025, 1, 15, 7, 30, tzinfo=UTC),
        )
        metrics = compute_time_metrics(
            route,
            MatrixResult(
                durations_seconds=[[0.0]],
                distances_meters=[[0.0]],
                provider="flat",
                cached=False,
            ),
            WorkRules(),
        )
        assert metrics["departure_times_seconds"] == [pytest.approx(7.75 * 3600)]


class TestMethodologyDisclosure:
    """The report must say which clock it graded on."""

    def test_profiled_report_states_the_baseline(self) -> None:
        text = _plain(_render(_fleet(), URBAN_US_PROFILE))
        assert "are not live or historical traffic data" in text

    def test_profiled_report_discloses_the_bands(self) -> None:
        text = _plain(_render(_fleet(), URBAN_US_PROFILE))
        assert "07:00" in text
        assert "16:00" in text
        assert "0.75" in text

    def test_profiled_report_documents_the_approximation(self) -> None:
        text = _plain(_render(_fleet(), URBAN_US_PROFILE))
        assert "single-pass approximation" in text.lower()
        assert "not iterated to a fixed point" in text

    def test_free_flow_report_carries_the_caveat(self) -> None:
        text = _plain(_render(_fleet(), None))
        assert "lower bounds" in text
        assert "No traffic profile was applied" in text

    def test_free_flow_report_keeps_the_hedge(self) -> None:
        """Boilerplate stays honest when nothing models traffic."""
        text = _plain(_render(_fleet(), None))
        assert "(traffic, weather, access restrictions) are not modeled" in text

    def test_profiled_report_drops_the_stale_hedge(self) -> None:
        """Claiming traffic is unmodeled is false once a profile is applied."""
        text = _plain(_render(_fleet(), URBAN_US_PROFILE))
        assert "(traffic, weather, access restrictions) are not modeled" not in text

    def test_report_without_traffic_metadata_falls_back_to_free_flow(self) -> None:
        """Replaying an analysis.json written before this phase must still render."""
        fleet = _fleet()
        fleet_metrics, route_metrics = compute_scorecard(fleet, FlatProvider(), AnalysisConfig())  # type: ignore[arg-type]
        report = AnalysisReport(
            fleet=fleet,
            fleet_metrics=fleet_metrics,
            route_metrics=route_metrics,
            findings=[],
            analyses_run=[],
            analyses_skipped=[],
            metadata={"orchestrator_model": "test"},
        )
        assert "No traffic profile was applied" in _plain(ReportDocument(report).render({}))
