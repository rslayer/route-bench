"""VRP with time windows solver using OR-Tools."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import numpy.typing as npt
from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # type: ignore[import-untyped]

from routebench.analysis.benchmark.windows import (
    SECONDS_PER_DAY,
    apply_time_windows,
    route_start_seconds,
)
from routebench.core.config import WorkRules
from routebench.core.schemas import Fleet, Stop
from routebench.infra.matrix.base import MatrixResult

SECONDS_PER_HOUR = 3600


class VehicleRoute(NamedTuple):
    """A vehicle's assigned stops in optimal order."""

    vehicle_id: int
    stop_indices: list[int]


class FleetSolution(NamedTuple):
    """Result of VRPTW optimization for a fleet."""

    vehicle_routes: list[VehicleRoute]
    total_distance_meters: float
    optimality_gap: float


def solve_vrptw(
    fleet: Fleet,
    combined_matrix: MatrixResult,
    work_rules: WorkRules,
    time_limit_s: int = 120,
) -> FleetSolution:
    """Solve VRPTW for a fleet using OR-Tools.

    The combined_matrix should be a single (1+total_stops) x (1+total_stops) matrix
    where index 0 is the shared depot and indices 1..N are all stops across all routes.

    The optimum is computed on whatever durations `combined_matrix` carries, so
    passing a traffic-adjusted matrix grades this benchmark on the same clock as
    the plan. Note for whoever builds that combined matrix: banding is per origin
    (see infra.matrix.traffic), and while each stop row has an unambiguous
    departure time from its own route's schedule, depot row 0 is shared across
    every vehicle and each route has its own planned_start_time. One factor must
    be chosen for it — OR-Tools arc costs are per node pair, not per vehicle.

    Args:
        fleet: The fleet to optimize.
        combined_matrix: Combined distance/duration matrix.
        work_rules: Shift constraints.
        time_limit_s: Solver time limit in seconds.

    Returns:
        FleetSolution with optimal assignments and gap.
    """
    n_vehicles = len(fleet.routes)
    all_stops: list[Stop] = []
    for route in fleet.routes:
        all_stops.extend(route.stops)

    n_stops = len(all_stops)
    if n_stops == 0:
        return FleetSolution(
            vehicle_routes=[],
            total_distance_meters=0.0,
            optimality_gap=0.0,
        )

    n_nodes = n_stops + 1  # depot + all stops
    dist_matrix = combined_matrix.distances_array()
    dur_matrix = combined_matrix.durations_array()

    int_dist = np.round(dist_matrix).astype(int).tolist()
    int_dur = np.round(dur_matrix).astype(int).tolist()

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)
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
        to_stop_idx = to_node - 1
        service = 0
        if 0 <= to_stop_idx < n_stops:
            service = int(all_stops[to_stop_idx].service_time_minutes * 60)
        return travel + service

    time_callback_index = routing.RegisterTransitCallback(time_callback)

    max_shift_seconds = int(work_rules.max_shift_hours * SECONDS_PER_HOUR)
    # Wall-clock seconds since midnight, not elapsed time, so a "09:00" window
    # compares directly against the dimension. Unlike the single-route solver,
    # each vehicle here has its own departure, so each start cumul is pinned
    # separately below.
    routing.AddDimension(
        time_callback_index,
        max_shift_seconds,  # slack: waiting for a window to open
        SECONDS_PER_DAY,  # max cumul: the wall clock, not the shift
        False,  # start cumul is the vehicle's departure, not zero
        "Time",
    )

    start_by_vehicle = {v: route_start_seconds(r) for v, r in enumerate(fleet.routes)}
    earliest_start = min(start_by_vehicle.values(), default=0)
    horizon_end = min(SECONDS_PER_DAY, earliest_start + max_shift_seconds)

    if work_rules.enforce_time_windows:
        apply_time_windows(
            routing,
            manager,
            "Time",
            stops_by_node={i + 1: stop for i, stop in enumerate(all_stops)},
            start_seconds_by_vehicle=start_by_vehicle,
            horizon_end=horizon_end,
        )
    else:
        time_dim = routing.GetDimensionOrDie("Time")
        for vehicle, start in start_by_vehicle.items():
            time_dim.CumulVar(routing.Start(vehicle)).SetRange(start, start)
            routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(vehicle)))
            routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(vehicle)))

    # Capacity constraints for dimensions that are present
    if work_rules.enforce_capacity:
        _add_capacity_constraints(routing, manager, fleet, all_stops, n_stops)

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
        # Return actual assignment as fallback
        return _actual_solution(fleet, dist_matrix)

    # Extract solution
    vehicle_routes: list[VehicleRoute] = []
    total_dist = 0.0

    for v in range(n_vehicles):
        stop_indices: list[int] = []
        index = routing.Start(v)
        prev_node = manager.IndexToNode(index)

        while not routing.IsEnd(index):
            index = solution.Value(routing.NextVar(index))
            node = manager.IndexToNode(index)
            if not routing.IsEnd(index):
                stop_indices.append(node)
            total_dist += float(dist_matrix[prev_node, node])
            prev_node = node

        vehicle_routes.append(VehicleRoute(vehicle_id=v, stop_indices=stop_indices))

    # Unclamped: negative means the solver found nothing better than the plan.
    actual_dist = _actual_fleet_distance(fleet, dist_matrix)
    gap = 0.0
    if total_dist > 0 and actual_dist > 0:
        gap = (actual_dist - total_dist) / actual_dist

    return FleetSolution(
        vehicle_routes=vehicle_routes,
        total_distance_meters=total_dist,
        optimality_gap=gap,
    )


def _add_capacity_constraints(
    routing: pywrapcp.RoutingModel,
    manager: pywrapcp.RoutingIndexManager,
    fleet: Fleet,
    all_stops: list[Stop],
    n_stops: int,
) -> None:
    """Add capacity constraints for unit/weight/volume dimensions if data exists."""
    # Check if any route has capacity data
    has_units = any(r.vehicle_capacity_units is not None for r in fleet.routes)
    if not has_units:
        return

    # Units dimension
    demands = [0]  # depot
    for stop in all_stops:
        demands.append(int(stop.demand_units or 0))

    def demand_callback(from_index: int) -> int:
        node: int = manager.IndexToNode(from_index)
        return int(demands[node])

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)

    capacities = []
    for route in fleet.routes:
        capacities.append(int(route.vehicle_capacity_units or 10000))

    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        capacities,
        True,
        "Capacity",
    )


def _actual_solution(
    fleet: Fleet,
    dist_matrix: npt.NDArray[np.float64],
) -> FleetSolution:
    """Build a FleetSolution from the actual assignments."""
    vehicle_routes: list[VehicleRoute] = []
    offset = 1
    total_dist = 0.0

    for v, route in enumerate(fleet.routes):
        n = len(route.stops)
        indices = list(range(offset, offset + n))
        vehicle_routes.append(VehicleRoute(vehicle_id=v, stop_indices=indices))

        if n > 0:
            total_dist += float(dist_matrix[0, offset])
            for i in range(n - 1):
                total_dist += float(dist_matrix[offset + i, offset + i + 1])
            total_dist += float(dist_matrix[offset + n - 1, 0])

        offset += n

    return FleetSolution(
        vehicle_routes=vehicle_routes,
        total_distance_meters=total_dist,
        optimality_gap=0.0,
    )


def _actual_fleet_distance(
    fleet: Fleet,
    dist_matrix: npt.NDArray[np.float64],
) -> float:
    """Compute actual total fleet distance."""
    total = 0.0
    offset = 1
    for route in fleet.routes:
        n = len(route.stops)
        if n > 0:
            total += float(dist_matrix[0, offset])
            for i in range(n - 1):
                total += float(dist_matrix[offset + i, offset + i + 1])
            total += float(dist_matrix[offset + n - 1, 0])
        offset += n
    return total
