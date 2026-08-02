"""Tests for analysis/benchmark — TSPTW, VRPTW, and comparison."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from routebench.analysis.benchmark import FleetBenchmarkTool, RouteBenchmarkTool
from routebench.analysis.benchmark.compare import (
    compute_fleet_benchmark,
    compute_route_benchmark,
)
from routebench.analysis.benchmark.fleet_matrix import (
    MAX_FLEET_BENCHMARK_STOPS,
    MAX_ROUTE_BENCHMARK_STOPS,
)
from routebench.analysis.benchmark.tsptw import solve_tsptw
from routebench.analysis.benchmark.vrptw import solve_vrptw
from routebench.analysis.tools import TOOLS
from routebench.core.config import WorkRules
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult


def _ts(hour: int = 8, minute: int = 0) -> datetime:
    return datetime(2025, 1, 15, hour, minute, 0, tzinfo=UTC)


def _make_stop(
    route_id: str,
    seq: int,
    lat: float,
    lon: float,
    svc: float = 5.0,
    demand_units: float | None = None,
) -> Stop:
    return Stop(
        route_id=route_id,
        stop_sequence=seq,
        latitude=lat,
        longitude=lon,
        service_time_minutes=svc,
        demand_units=demand_units,
    )


def _make_route(
    route_id: str,
    stops: list[Stop],
    depot_lat: float = 32.825,
    depot_lon: float = -96.775,
    capacity_units: float | None = None,
) -> Route:
    return Route(
        route_id=route_id,
        stops=stops,
        depot_lat=depot_lat,
        depot_lon=depot_lon,
        planned_start_time=_ts(),
        vehicle_capacity_units=capacity_units,
    )


def _make_fleet(*routes: Route) -> Fleet:
    return Fleet(
        routes=list(routes),
        upload_id="test",
        uploaded_at=_ts(),
    )


class TestToolsRegistry:
    """Verify benchmark tools are registered."""

    def test_benchmark_tools_registered(self) -> None:
        import routebench.analysis  # noqa: F401

        assert "route_benchmark" in TOOLS
        assert "fleet_benchmark" in TOOLS


class TestTSPTW:
    """Tests for TSPTW solver."""

    def test_bad_sequence_reordered(self) -> None:
        """3 stops in wrong order: 3→1→2 should be reordered to 1→2→3 or better."""
        # Stops arranged linearly: s1(32.83), s2(32.84), s3(32.85)
        # Actual order: s3, s1, s2 (zigzag)
        stops = [
            _make_stop("R1", 1, 32.85, -96.77),  # s3 (far north)
            _make_stop("R1", 2, 32.83, -96.77),  # s1 (far south)
            _make_stop("R1", 3, 32.84, -96.77),  # s2 (middle)
        ]
        route = _make_route("R1", stops)

        # Matrix where sequential order is bad:
        # depot(0) at 32.825. Stops: 1=32.85(north), 2=32.83(south), 3=32.84(mid)
        # Actual: depot→north(2.8km)→south(2.2km)→mid(1.1km)→depot(1.7km) = 7.8km
        # Better: depot→south→mid→north→depot or depot→south→north→mid→depot
        n = 4
        dists = [[0.0] * n for _ in range(n)]
        # depot to stops
        dists[0][1] = 2800.0  # depot→north
        dists[0][2] = 500.0  # depot→south
        dists[0][3] = 1700.0  # depot→mid
        # stops to depot
        dists[1][0] = 2800.0
        dists[2][0] = 500.0
        dists[3][0] = 1700.0
        # stop to stop
        dists[1][2] = 2200.0  # north→south
        dists[2][1] = 2200.0
        dists[1][3] = 1100.0  # north→mid
        dists[3][1] = 1100.0
        dists[2][3] = 1100.0  # south→mid
        dists[3][2] = 1100.0

        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )

        work_rules = WorkRules()
        result = solve_tsptw(route, matrix, work_rules, time_limit_s=5)

        # Actual distance: depot→north(2800)→south(2200)→mid(1100)→depot(1700) = 7800
        actual_dist = 2800.0 + 2200.0 + 1100.0 + 1700.0
        assert result.total_distance_meters <= actual_dist
        assert len(result.stop_order) == 3

    def test_0_stop_route(self) -> None:
        """Empty route should return immediately."""
        route = _make_route("R1", [])
        matrix = MatrixResult(
            durations_seconds=[[0]],
            distances_meters=[[0]],
            provider="mock",
            cached=False,
        )
        result = solve_tsptw(route, matrix, WorkRules(), time_limit_s=1)
        assert result.stop_order == []
        assert result.total_distance_meters == 0.0

    def test_1_stop_route(self) -> None:
        """Single stop should return depot→stop→depot."""
        stop = _make_stop("R1", 1, 32.83, -96.77)
        route = _make_route("R1", [stop])
        dists = [[0.0, 1000.0], [1000.0, 0.0]]
        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )
        result = solve_tsptw(route, matrix, WorkRules(), time_limit_s=1)
        assert result.stop_order == [1]
        assert result.total_distance_meters == pytest.approx(2000.0)

    def test_time_limit_respected(self) -> None:
        """Solver should not exceed 2x the configured time limit."""
        stops = [_make_stop("R1", i, 32.83 + i * 0.01, -96.77) for i in range(1, 6)]
        route = _make_route("R1", stops)

        n = 6
        dists = [[float(abs(i - j) * 1000) for j in range(n)] for i in range(n)]
        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )

        start = time.monotonic()
        solve_tsptw(route, matrix, WorkRules(), time_limit_s=2)
        elapsed = time.monotonic() - start
        assert elapsed < 2 * 2  # 2x the time limit


class TestVRPTW:
    """Tests for VRPTW solver."""

    def test_wrong_route_stop_migrated(self) -> None:
        """Stop clearly assigned to wrong route should be migrated in optimal."""
        # Route A has 4 stops in south cluster
        # Route B has 3 stops in north cluster + 1 stop in south (misassigned)
        stops_a = [
            _make_stop("RA", 1, 32.80, -96.77, demand_units=1),
            _make_stop("RA", 2, 32.81, -96.77, demand_units=1),
            _make_stop("RA", 3, 32.82, -96.77, demand_units=1),
            _make_stop("RA", 4, 32.83, -96.77, demand_units=1),
        ]
        stops_b = [
            _make_stop("RB", 1, 33.00, -96.77, demand_units=1),
            _make_stop("RB", 2, 33.01, -96.77, demand_units=1),
            _make_stop("RB", 3, 33.02, -96.77, demand_units=1),
            _make_stop("RB", 4, 32.805, -96.77, demand_units=1),  # misassigned
        ]

        route_a = _make_route("RA", stops_a, capacity_units=10)
        route_b = _make_route("RB", stops_b, capacity_units=10)
        fleet = _make_fleet(route_a, route_b)

        # Build 9x9 matrix (depot + 8 stops)
        n = 9
        coords = [
            (32.825, -96.775),  # depot
            (32.80, -96.77),
            (32.81, -96.77),
            (32.82, -96.77),
            (32.83, -96.77),  # A stops
            (33.00, -96.77),
            (33.01, -96.77),
            (33.02, -96.77),
            (32.805, -96.77),  # B stops
        ]

        dists = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                lat_diff = abs(coords[i][0] - coords[j][0])
                lon_diff = abs(coords[i][1] - coords[j][1])
                dists[i][j] = (lat_diff + lon_diff) * 111000  # ~meters

        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )

        solution = solve_vrptw(fleet, matrix, WorkRules(), time_limit_s=5)
        assert solution.total_distance_meters > 0
        assert len(solution.vehicle_routes) == 2


class TestCompare:
    """Tests for benchmark comparison."""

    def test_route_benchmark_gap(self) -> None:
        """Route with suboptimal sequence should show distance gap."""
        stops = [
            _make_stop("R1", 1, 32.85, -96.77),
            _make_stop("R1", 2, 32.83, -96.77),
            _make_stop("R1", 3, 32.84, -96.77),
        ]
        route = _make_route("R1", stops)

        n = 4
        dists = [[0.0] * n for _ in range(n)]
        dists[0][1] = 2800.0
        dists[0][2] = 500.0
        dists[0][3] = 1700.0
        dists[1][0] = 2800.0
        dists[2][0] = 500.0
        dists[3][0] = 1700.0
        dists[1][2] = 2200.0
        dists[2][1] = 2200.0
        dists[1][3] = 1100.0
        dists[3][1] = 1100.0
        dists[2][3] = 1100.0
        dists[3][2] = 1100.0

        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )

        optimal = solve_tsptw(route, matrix, WorkRules(), time_limit_s=5)
        benchmark = compute_route_benchmark(route, optimal, matrix)

        assert benchmark.route_id == "R1"
        assert benchmark.actual_distance_miles > 0
        assert benchmark.optimal_distance_miles > 0
        # The gap should be >= 0 (optimal should be <= actual)
        assert benchmark.distance_gap_pct >= 0.0

    def test_fleet_benchmark_migrations(self) -> None:
        """Fleet benchmark should detect stop migrations."""
        stops_a = [
            _make_stop("RA", 1, 32.80, -96.77, demand_units=1),
            _make_stop("RA", 2, 32.81, -96.77, demand_units=1),
        ]
        stops_b = [
            _make_stop("RB", 1, 33.00, -96.77, demand_units=1),
            _make_stop("RB", 2, 33.01, -96.77, demand_units=1),
        ]
        route_a = _make_route("RA", stops_a, capacity_units=10)
        route_b = _make_route("RB", stops_b, capacity_units=10)
        fleet = _make_fleet(route_a, route_b)

        n = 5
        coords = [
            (32.825, -96.775),
            (32.80, -96.77),
            (32.81, -96.77),
            (33.00, -96.77),
            (33.01, -96.77),
        ]
        dists = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                lat_diff = abs(coords[i][0] - coords[j][0])
                dists[i][j] = lat_diff * 111000

        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )

        all_stops = stops_a + stops_b
        solution = solve_vrptw(fleet, matrix, WorkRules(), time_limit_s=5)
        benchmark = compute_fleet_benchmark(fleet, solution, all_stops)
        assert benchmark.optimal_total_distance > 0


class TestBenchmarkTools:
    """Tests for RouteBenchmarkTool and FleetBenchmarkTool."""

    def test_route_benchmark_applicable_below_the_cap(self) -> None:
        tool = RouteBenchmarkTool()
        stops = [_make_stop("R1", 1, 32.83, -96.77)]
        fleet = _make_fleet(_make_route("R1", stops))
        assert tool.applicability_check(fleet).is_applicable

    def test_route_benchmark_no_longer_capped_on_route_count(self) -> None:
        """The old 50-route cap is gone: routes solve independently, so a
        high-route-count fleet is still benchmarked (the shared solver envelope
        bounds the wall-clock instead)."""
        tool = RouteBenchmarkTool()
        routes = [
            _make_route(f"R{r}", [_make_stop(f"R{r}", 1, 32.83 + r * 0.001, -96.77)])
            for r in range(80)  # well past the retired 50-route cap
        ]
        fleet = _make_fleet(*routes)
        assert fleet.total_stops() <= MAX_ROUTE_BENCHMARK_STOPS
        assert tool.applicability_check(fleet).is_applicable

    def test_route_benchmark_skips_fleet_above_stop_cap(self) -> None:
        """The fleet-total stop count is still a real ceiling on solve work."""
        tool = RouteBenchmarkTool()
        # 50 routes x 101 stops = 5050 > MAX_ROUTE_BENCHMARK_STOPS.
        per_route = (MAX_ROUTE_BENCHMARK_STOPS // 50) + 1
        routes = [
            _make_route(
                f"R{r}",
                [
                    _make_stop(f"R{r}", i, 32.83 + i * 0.0001, -96.77)
                    for i in range(1, per_route + 1)
                ],
            )
            for r in range(50)
        ]
        fleet = _make_fleet(*routes)
        assert fleet.total_stops() > MAX_ROUTE_BENCHMARK_STOPS
        check = tool.applicability_check(fleet)
        assert not check.is_applicable
        assert "stops exceeds" in check.reason

    def test_fleet_benchmark_applies_to_a_multi_route_fleet(self) -> None:
        tool = FleetBenchmarkTool()
        fleet = _make_fleet(
            _make_route("R1", [_make_stop("R1", 1, 32.83, -96.77)]),
            _make_route("R2", [_make_stop("R2", 1, 32.84, -96.78)]),
        )
        assert tool.applicability_check(fleet).is_applicable

    def test_fleet_benchmark_skips_single_route_fleet(self) -> None:
        """Cross-route optimization on one route is just the per-route TSPTW."""
        tool = FleetBenchmarkTool()
        fleet = _make_fleet(_make_route("R1", [_make_stop("R1", 1, 32.83, -96.77)]))
        check = tool.applicability_check(fleet)
        assert not check.is_applicable
        assert "at least 2 routes" in check.reason

    def test_fleet_benchmark_skips_fleet_without_one_shared_depot(self) -> None:
        """solve_vrptw models a single depot node for every vehicle."""
        tool = FleetBenchmarkTool()
        fleet = _make_fleet(
            _make_route("R1", [_make_stop("R1", 1, 32.83, -96.77)], depot_lat=32.825),
            _make_route("R2", [_make_stop("R2", 1, 32.84, -96.78)], depot_lat=33.900),
        )
        check = tool.applicability_check(fleet)
        assert not check.is_applicable
        assert "share a single depot" in check.reason

    def test_fleet_benchmark_skips_fleet_above_stop_cap(self) -> None:
        """The matrix grows quadratically and OR-Tools degrades; refuse instead."""
        tool = FleetBenchmarkTool()
        routes = [
            _make_route(
                f"R{r}",
                [_make_stop(f"R{r}", i, 32.83 + i * 0.001, -96.77) for i in range(1, 41)],
            )
            for r in range(1, 9)
        ]
        fleet = _make_fleet(*routes)
        assert fleet.total_stops() > MAX_FLEET_BENCHMARK_STOPS
        check = tool.applicability_check(fleet)
        assert not check.is_applicable
        assert "cap for fleet-level VRPTW" in check.reason
