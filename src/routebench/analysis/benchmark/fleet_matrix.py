"""Combined fleet matrix construction for VRPTW benchmarking.

The VRPTW solver optimizes across routes, so it needs one matrix spanning the
whole fleet: index 0 is the shared depot, indices 1..N are every stop in fleet
order. This is a different, larger matrix than the per-route ones the scorecard
uses, and it is only built when the fleet benchmark actually runs.
"""

from __future__ import annotations

from datetime import datetime

import structlog

from routebench.analysis.scoring.time import compute_departure_schedule
from routebench.core.config import TrafficProfile, WorkRules
from routebench.core.exceptions import InvalidInputError
from routebench.core.schemas import Fleet
from routebench.infra.matrix.base import MatrixProvider, MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ~0.1m at the equator. Depots are one physical yard; anything above this is a
# genuinely different location, not float noise.
DEPOT_TOLERANCE_DEG = 1e-6

# OR-Tools degrades and the matrix grows quadratically (N stops -> (N+1)^2 cells),
# so refuse rather than melt. 300 stops is ~90k cells and solves inside the
# configured time limit.
MAX_FLEET_BENCHMARK_STOPS = 300

# Per-route re-solve stop cap. The route benchmark solves one TSPTW per route,
# each to a limit divided out of a shared solver envelope (see
# route_time_limit_s), so route COUNT is bounded by that envelope, not by a cap.
# Total stop count is still a real ceiling on the matrix + solve work; above it
# the re-solve is skipped and the fleet is analysed descriptively (the grade
# falls back to a nearest-neighbour sequencing baseline). Set to the historical
# hard-reject limit so behaviour at or below it is unchanged; larger fleets now
# degrade here instead of being rejected at validation.
MAX_ROUTE_BENCHMARK_STOPS = 5_000


def fleet_depot(fleet: Fleet) -> tuple[float, float] | None:
    """Return the fleet's single shared depot, or None if routes disagree.

    solve_vrptw models one depot node for all vehicles, so a fleet whose routes
    start from different yards cannot be benchmarked as one problem.
    """
    if not fleet.routes:
        return None

    first = fleet.routes[0]
    depot = (first.depot_lat, first.depot_lon)
    for route in fleet.routes[1:]:
        if (
            abs(route.depot_lat - depot[0]) > DEPOT_TOLERANCE_DEG
            or abs(route.depot_lon - depot[1]) > DEPOT_TOLERANCE_DEG
        ):
            return None
    return depot


def fleet_coords(fleet: Fleet) -> list[tuple[float, float]]:
    """Depot followed by every stop, in fleet order. Index 0 is the depot."""
    depot = fleet_depot(fleet)
    if depot is None:
        msg = "Fleet routes do not share a single depot; cannot build a combined matrix"
        raise InvalidInputError(msg, field="depot")

    coords = [depot]
    for route in fleet.routes:
        coords.extend((stop.latitude, stop.longitude) for stop in route.stops)
    return coords


def _median_datetime(values: list[datetime]) -> datetime:
    """Lower median. Used to collapse per-vehicle depot departures into one."""
    return sorted(values)[len(values) // 2]


def combined_departure_schedule(
    fleet: Fleet,
    matrix_provider: MatrixProvider,
    work_rules: WorkRules,
) -> list[datetime]:
    """Estimated departure time per combined-matrix index.

    Each stop is banded by its own route's free-flow schedule, which is
    unambiguous. Depot row 0 is not: it is shared by every vehicle, and each
    route has its own planned_start_time. OR-Tools arc costs are per node pair,
    not per vehicle, so one factor must cover them all — we take the median
    depot departure. The cost is confined to the depot-to-first-stop leg of
    routes whose start times sit in a different band from the median, and the
    methodology section discloses it.
    """
    depot_departures: list[datetime] = []
    stop_departures: list[datetime] = []

    for route in fleet.routes:
        coords = [(route.depot_lat, route.depot_lon)]
        coords.extend((stop.latitude, stop.longitude) for stop in route.stops)

        # No departure times => free-flow, which is what the single-pass
        # approximation propagates from. Providers memoize, so this is the same
        # fetch the scorecard already made for this route.
        free_flow = matrix_provider.get_matrix(coords, coords)
        schedule = compute_departure_schedule(route, free_flow, work_rules)

        depot_departures.append(schedule[0])
        stop_departures.extend(schedule[1:])

    return [_median_datetime(depot_departures), *stop_departures]


def get_fleet_matrix(
    fleet: Fleet,
    matrix_provider: MatrixProvider,
    work_rules: WorkRules | None = None,
) -> MatrixResult:
    """Build the combined (1+N)x(1+N) fleet matrix, traffic-banded if applicable.

    Mirrors get_route_matrix's two-pass shape: a free-flow pass to estimate
    departures, then a banded pass. Raises RouteBenchError if the fleet's routes
    do not share one depot.
    """
    coords = fleet_coords(fleet)

    # One matrix spans every route, so there is no single start time — use the
    # median of the routes' start times as the representative departure, the
    # same lower-median this module already uses to collapse per-vehicle depot
    # departures. Only computed for a time-aware engine; OSRM/haversine are not
    # handed it, leaving their cache unfragmented. Passed on both passes so the
    # banded pass shares the free-flow pass's memo/cache key.
    depart = (
        _median_datetime([r.planned_start_time for r in fleet.routes])
        if getattr(matrix_provider, "is_time_aware", False)
        else None
    )

    free_flow = matrix_provider.get_matrix(coords, coords, departure_time=depart)

    profile = getattr(matrix_provider, "profile", None)
    banding = isinstance(profile, TrafficProfile) and profile.is_active

    if banding and work_rules is None:
        logger.warning(
            "fleet_traffic_profile_ignored_without_work_rules",
            provider=getattr(matrix_provider, "name", "unknown"),
        )

    if not banding or work_rules is None:
        return free_flow

    departures = combined_departure_schedule(fleet, matrix_provider, work_rules)
    logger.info(
        "fleet_matrix_banded",
        n_nodes=len(coords),
        depot_departure=departures[0].isoformat(),
    )
    return matrix_provider.get_matrix(
        coords, coords, departure_time=depart, origin_departure_times=departures
    )
