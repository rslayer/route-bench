"""Phase 10.5 Part B: the benchmark says what it means.

The field used to be called optimality_gap_reported_by_solver, which claimed a
bound the solver never proves. It measures improvement over the plan, it is a
percentage, and it can be negative.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from routebench.analysis.benchmark import RouteBenchmarkTool
from routebench.analysis.benchmark.compare import compute_route_benchmark
from routebench.analysis.benchmark.tsptw import OptimalSequence
from routebench.core.findings import FleetBenchmark, RouteBenchmark
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult

METERS_PER_MILE = 1609.34


def _route(n_stops: int = 3) -> Route:
    return Route(
        route_id="R1",
        stops=[
            Stop(
                route_id="R1",
                stop_sequence=i,
                latitude=32.80 + 0.01 * i,
                longitude=-96.80,
                service_time_minutes=5.0,
            )
            for i in range(1, n_stops + 1)
        ],
        depot_lat=32.79,
        depot_lon=-96.80,
        planned_start_time=datetime(2025, 1, 15, 8, 0, tzinfo=UTC),
    )


def _matrix(n: int, distance_m: float = 1000.0, duration_s: float = 300.0) -> MatrixResult:
    return MatrixResult(
        durations_seconds=[[duration_s] * n for _ in range(n)],
        distances_meters=[[distance_m] * n for _ in range(n)],
        provider="flat",
        cached=False,
    )


class TestFieldNaming:
    """The contract Phase 11 freezes must not carry the old lie."""

    def test_route_benchmark_uses_improvement_gap_pct(self) -> None:
        assert "improvement_gap_pct" in RouteBenchmark.model_fields
        assert "optimality_gap_reported_by_solver" not in RouteBenchmark.model_fields

    def test_fleet_benchmark_uses_improvement_gap_pct(self) -> None:
        assert "improvement_gap_pct" in FleetBenchmark.model_fields
        assert "optimality_gap_reported_by_solver" not in FleetBenchmark.model_fields


class TestPercentageUnits:
    """improvement_gap_pct is a percentage, like its sibling gap fields."""

    def test_solver_fraction_becomes_a_percentage(self) -> None:
        """A 0.12 solver fraction is 12%, not 0.12%."""
        route = _route(3)
        matrix = _matrix(4)
        optimal = OptimalSequence(
            stop_order=[1, 2, 3],
            total_distance_meters=3520.0,
            total_time_seconds=1200.0,
            optimality_gap=0.12,
        )
        benchmark = compute_route_benchmark(route, optimal, matrix)
        assert benchmark.improvement_gap_pct == pytest.approx(12.0)


class TestNegativeGaps:
    """A plan the solver cannot beat is a result, not an error."""

    def test_negative_distance_gap_is_preserved(self) -> None:
        """The solver returning a longer tour must not be clamped to zero."""
        route = _route(3)
        matrix = _matrix(4, distance_m=1000.0)
        # Actual tour = 4 legs x 1000m; solver "found" something worse.
        optimal = OptimalSequence(
            stop_order=[1, 2, 3],
            total_distance_meters=5000.0,
            total_time_seconds=1500.0,
            optimality_gap=-0.25,
        )
        benchmark = compute_route_benchmark(route, optimal, matrix)
        assert benchmark.distance_gap_pct < 0
        assert benchmark.improvement_gap_pct == pytest.approx(-25.0)

    def test_zero_gap_when_solver_matches_plan(self) -> None:
        route = _route(3)
        matrix = _matrix(4, distance_m=1000.0)
        optimal = OptimalSequence(
            stop_order=[1, 2, 3],
            total_distance_meters=4000.0,
            total_time_seconds=1200.0,
            optimality_gap=0.0,
        )
        benchmark = compute_route_benchmark(route, optimal, matrix)
        assert benchmark.distance_gap_pct == pytest.approx(0.0)


class TestNoMaterialSavingsBranch:
    """The plan-is-fine outcome is reported, not silently dropped."""

    def test_matching_plan_emits_an_info_finding(self) -> None:
        """A uniform matrix gives the solver nothing to improve."""
        fleet = Fleet(
            routes=[_route(3)],
            upload_id="t",
            uploaded_at=datetime(2025, 1, 15, tzinfo=UTC),
        )
        findings = RouteBenchmarkTool().run(fleet, matrices={"R1": _matrix(4)}, time_limit_s=1)

        no_savings = [f for f in findings if f.severity == "info"]
        assert no_savings, "expected a no-material-savings finding"
        assert "within solver reach" in no_savings[0].title
        assert no_savings[0].category == "sequencing"

    def test_no_savings_finding_carries_the_gap_as_evidence(self) -> None:
        fleet = Fleet(
            routes=[_route(3)],
            upload_id="t",
            uploaded_at=datetime(2025, 1, 15, tzinfo=UTC),
        )
        findings = RouteBenchmarkTool().run(fleet, matrices={"R1": _matrix(4)}, time_limit_s=1)
        info = next(f for f in findings if f.severity == "info")
        assert info.evidence[0].metric_name == "distance_gap_pct"
        assert info.evidence[0].actual_value <= 0.0
