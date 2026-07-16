"""Benchmark comparison: actual vs optimal route/fleet metrics."""

from __future__ import annotations

from routebench.analysis.benchmark.tsptw import OptimalSequence, _actual_distance, _actual_time
from routebench.analysis.benchmark.vrptw import FleetSolution
from routebench.core.findings import (
    FleetBenchmark,
    RouteBenchmark,
    StopMigration,
)
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult

METERS_PER_MILE = 1609.34
SECONDS_PER_HOUR = 3600.0


def compute_route_benchmark(
    route: Route,
    optimal: OptimalSequence,
    matrix: MatrixResult,
) -> RouteBenchmark:
    """Compare actual route to optimal TSPTW solution."""
    dist_matrix = matrix.distances_array()
    dur_matrix = matrix.durations_array()
    n_stops = len(route.stops)

    actual_dist = _actual_distance(dist_matrix, n_stops)
    actual_time = _actual_time(dur_matrix, n_stops)

    optimal_dist = optimal.total_distance_meters
    optimal_time = optimal.total_time_seconds

    # Not clamped at zero: a negative gap means the solver found nothing better
    # than the plan. That is a legitimate, reportable outcome — clamping it would
    # silently render a good plan as merely "0% wasteful".
    dist_gap = (actual_dist - optimal_dist) / actual_dist * 100 if actual_dist > 0 else 0.0
    time_gap = (actual_time - optimal_time) / actual_time * 100 if actual_time > 0 else 0.0

    return RouteBenchmark(
        route_id=route.route_id,
        actual_distance_miles=actual_dist / METERS_PER_MILE,
        optimal_distance_miles=optimal_dist / METERS_PER_MILE,
        distance_gap_pct=dist_gap,
        actual_time_hours=actual_time / SECONDS_PER_HOUR,
        optimal_time_hours=optimal_time / SECONDS_PER_HOUR,
        time_gap_pct=time_gap,
        # Solvers report a fraction; this field is a percentage, like its siblings.
        improvement_gap_pct=optimal.optimality_gap * 100.0,
        # Carry the solver's tour through. It costs the whole benchmark to
        # compute and was previously discarded, leaving consumers told a better
        # sequence exists but never shown it.
        stop_order=list(optimal.stop_order),
    )


def compute_fleet_benchmark(
    fleet: Fleet,
    solution: FleetSolution,
    all_stops: list[Stop],
    per_route_matrices: dict[str, MatrixResult] | None = None,
) -> FleetBenchmark:
    """Compare actual fleet to optimal VRPTW solution.

    Computes stop migrations (which stops moved between routes).
    """
    # Build actual assignment: stop_global_index -> route_id
    actual_assignment: dict[int, str] = {}
    idx = 1
    for route in fleet.routes:
        for _stop in route.stops:
            actual_assignment[idx] = route.route_id
            idx += 1

    # Build optimal assignment from solution
    optimal_assignment: dict[int, str] = {}
    for vr in solution.vehicle_routes:
        if vr.vehicle_id < len(fleet.routes):
            route_id = fleet.routes[vr.vehicle_id].route_id
        else:
            route_id = f"vehicle_{vr.vehicle_id}"
        for stop_idx in vr.stop_indices:
            optimal_assignment[stop_idx] = route_id

    # Compute migrations
    migrations: list[StopMigration] = []
    idx = 1
    for route in fleet.routes:
        for stop in route.stops:
            actual_route = actual_assignment.get(idx, route.route_id)
            optimal_route = optimal_assignment.get(idx, actual_route)
            if actual_route != optimal_route:
                migrations.append(
                    StopMigration(
                        route_id=actual_route,
                        stop_sequence=stop.stop_sequence,
                        customer_id=stop.customer_id,
                        from_route=actual_route,
                        to_route=optimal_route,
                    )
                )
            idx += 1

    # Compute actual total distance from per-route matrices
    actual_total_miles = 0.0
    if per_route_matrices:
        for route in fleet.routes:
            matrix = per_route_matrices.get(route.route_id)
            if matrix is not None:
                n = len(route.stops)
                if n > 0:
                    dist = _actual_distance(matrix.distances_array(), n)
                    actual_total_miles += dist / METERS_PER_MILE

    return FleetBenchmark(
        actual_total_distance=actual_total_miles,
        optimal_total_distance=solution.total_distance_meters / METERS_PER_MILE,
        stop_migrations=migrations,
        # Solvers report a fraction; this field is a percentage, like its siblings.
        improvement_gap_pct=solution.optimality_gap * 100.0,
    )
