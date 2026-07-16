"""TSP with time windows solver using OR-Tools."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import numpy.typing as npt
import structlog
from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # type: ignore[import-untyped]

from routebench.analysis.benchmark.windows import (
    SECONDS_PER_DAY,
    apply_time_windows,
    route_start_seconds,
)
from routebench.core.config import WorkRules
from routebench.core.schemas import Route
from routebench.infra.matrix.base import MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

SECONDS_PER_HOUR = 3600


class OptimalSequence(NamedTuple):
    """Result of TSPTW optimization for a single route."""

    stop_order: list[int]
    total_distance_meters: float
    total_time_seconds: float
    optimality_gap: float


def solve_tsptw(
    route: Route,
    matrix: MatrixResult,
    work_rules: WorkRules,
    time_limit_s: int = 30,
) -> OptimalSequence:
    """Solve TSP with time windows for a single route using OR-Tools.

    The optimum is computed on whatever durations `matrix` carries. When the
    caller supplies a traffic-adjusted matrix, the plan and this benchmark are
    graded on the same clock, so the reported gap stays a like-for-like
    comparison. Band assignment is fixed per origin before solving, so it does
    not shift as the solver resequences (see infra.matrix.traffic).

    Args:
        route: The route to optimize.
        matrix: Distance/duration matrix (depot at index 0, stops at 1..n).
        work_rules: Shift constraints.
        time_limit_s: Solver time limit in seconds.

    Returns:
        OptimalSequence with optimal stop ordering and gap.
    """
    n_stops = len(route.stops)
    if n_stops == 0:
        return OptimalSequence(
            stop_order=[],
            total_distance_meters=0.0,
            total_time_seconds=0.0,
            optimality_gap=0.0,
        )

    if n_stops == 1:
        dist = float(matrix.distances_array()[0, 1]) + float(matrix.distances_array()[1, 0])
        dur = float(matrix.durations_array()[0, 1]) + float(matrix.durations_array()[1, 0])
        return OptimalSequence(
            stop_order=[1],
            total_distance_meters=dist,
            total_time_seconds=dur,
            optimality_gap=0.0,
        )

    n_nodes = n_stops + 1  # depot + stops
    dist_matrix = matrix.distances_array()
    dur_matrix = matrix.durations_array()

    # Convert to integer (OR-Tools uses int)
    int_dist = np.round(dist_matrix).astype(int).tolist()
    int_dur = np.round(dur_matrix).astype(int).tolist()

    manager = pywrapcp.RoutingIndexManager(n_nodes, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    # Distance callback
    def distance_callback(from_index: int, to_index: int) -> int:
        from_node: int = manager.IndexToNode(from_index)
        to_node: int = manager.IndexToNode(to_index)
        return int(int_dist[from_node][to_node])

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Time dimension
    def time_callback(from_index: int, to_index: int) -> int:
        from_node: int = manager.IndexToNode(from_index)
        to_node: int = manager.IndexToNode(to_index)
        travel = int(int_dur[from_node][to_node])
        # Add service time at destination
        to_stop_idx = to_node - 1
        service = 0
        if 0 <= to_stop_idx < n_stops:
            service = int(route.stops[to_stop_idx].service_time_minutes * 60)
        return travel + service

    time_callback_index = routing.RegisterTransitCallback(time_callback)

    max_shift_seconds = int(work_rules.max_shift_hours * SECONDS_PER_HOUR)
    start_seconds = route_start_seconds(route)
    # The dimension carries wall-clock seconds since midnight, not elapsed time,
    # so a "09:00" window can be compared against it directly. That means the
    # horizon must span the day rather than the shift, and the start cumul
    # cannot be forced to zero — apply_time_windows pins it to the planned
    # departure instead.
    horizon_end = min(SECONDS_PER_DAY, start_seconds + max_shift_seconds)
    routing.AddDimension(
        time_callback_index,
        max_shift_seconds,  # slack: waiting for a window to open
        SECONDS_PER_DAY,  # max cumul: the wall clock, not the shift
        False,  # start cumul is the departure time, not zero
        "Time",
    )

    if work_rules.enforce_time_windows:
        applied = apply_time_windows(
            routing,
            manager,
            "Time",
            stops_by_node={i + 1: stop for i, stop in enumerate(route.stops)},
            start_seconds_by_vehicle={0: start_seconds},
            horizon_end=horizon_end,
        )
        if applied:
            logger.debug(
                "tsptw_time_windows_applied",
                route_id=route.route_id,
                n_windows=applied,
            )
    else:
        # Windows off: still pin the start so the shift cap is measured from the
        # real departure rather than from midnight.
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.CumulVar(routing.Start(0)).SetRange(start_seconds, start_seconds)
        time_dim.CumulVar(routing.End(0)).SetRange(start_seconds, horizon_end)

    # Warm-start: actual sequence as initial hint
    initial_routes = [[manager.NodeToIndex(i) for i in range(1, n_nodes)]]
    routing.ReadAssignmentFromRoutes(initial_routes, True)

    # Search parameters
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = time_limit_s

    solution = routing.SolveWithParameters(search_params)

    if solution is None:
        # If no solution, try without initial assignment
        solution = routing.SolveWithParameters(search_params)

    if solution is None:
        # Return actual sequence as fallback
        return OptimalSequence(
            stop_order=list(range(1, n_nodes)),
            total_distance_meters=_actual_distance(dist_matrix, n_stops),
            total_time_seconds=_actual_time(dur_matrix, n_stops),
            optimality_gap=0.0,
        )

    # Extract solution
    stop_order: list[int] = []
    total_dist = 0.0
    index = routing.Start(0)
    prev_node = manager.IndexToNode(index)

    while not routing.IsEnd(index):
        index = solution.Value(routing.NextVar(index))
        node = manager.IndexToNode(index)
        if not routing.IsEnd(index):
            stop_order.append(node)
        total_dist += float(dist_matrix[prev_node, node])
        prev_node = node

    # Add return to depot
    total_dist += float(dist_matrix[prev_node, 0]) if prev_node != 0 else 0.0

    total_time = float(solution.ObjectiveValue())

    # Compute optimality gap
    # Unclamped: negative means the solver found nothing better than the plan.
    actual_dist = _actual_distance(dist_matrix, n_stops)
    gap = 0.0
    if total_dist > 0 and actual_dist > 0:
        gap = (actual_dist - total_dist) / actual_dist

    return OptimalSequence(
        stop_order=stop_order,
        total_distance_meters=total_dist,
        total_time_seconds=total_time,
        optimality_gap=gap,
    )


def _actual_distance(dist_matrix: npt.NDArray[np.float64], n_stops: int) -> float:
    """Compute actual route distance from sequential traversal."""
    total = float(dist_matrix[0, 1])
    for i in range(1, n_stops):
        total += float(dist_matrix[i, i + 1])
    total += float(dist_matrix[n_stops, 0])
    return total


def _actual_time(dur_matrix: npt.NDArray[np.float64], n_stops: int) -> float:
    """Compute actual route travel time from sequential traversal."""
    total = float(dur_matrix[0, 1])
    for i in range(1, n_stops):
        total += float(dur_matrix[i, i + 1])
    total += float(dur_matrix[n_stops, 0])
    return total
