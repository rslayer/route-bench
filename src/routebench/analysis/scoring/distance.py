"""Distance scoring: compute route distance metrics from matrix data."""

from __future__ import annotations

import structlog

from routebench.analysis.scoring.time import compute_departure_schedule
from routebench.core.config import TrafficProfile, WorkRules
from routebench.core.schemas import Route
from routebench.infra.matrix.base import MatrixProvider, MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

METERS_PER_MILE = 1609.34


def compute_distance_metrics(route: Route, matrix: MatrixResult) -> dict[str, object]:
    """Compute distance metrics for a single route.

    Expects `matrix` to be a square (n+1)x(n+1) matrix where index 0 is the
    depot and indices 1..n are the stops in sequence order.

    Returns:
        Dict with:
        - total_distance_miles: sequential sum depot→s1→s2→...→sN→depot
        - leg_distances_miles: list of per-leg distances
        - avg_inter_stop_distance_miles: mean of stop-to-stop legs (excl depot legs)
    """
    n_stops = len(route.stops)
    if n_stops == 0:
        return {
            "total_distance_miles": 0.0,
            "leg_distances_miles": [],
            "avg_inter_stop_distance_miles": 0.0,
        }

    distances = matrix.distances_array()

    # Build the leg sequence: depot(0) → stop1(1) → stop2(2) → ... → stopN(n) → depot(0)
    leg_distances_miles: list[float] = []

    # Depot to first stop
    leg_distances_miles.append(float(distances[0, 1]) / METERS_PER_MILE)

    # Stop-to-stop legs
    for i in range(1, n_stops):
        leg_distances_miles.append(float(distances[i, i + 1]) / METERS_PER_MILE)

    # Last stop back to depot
    leg_distances_miles.append(float(distances[n_stops, 0]) / METERS_PER_MILE)

    total_distance_miles = sum(leg_distances_miles)

    # Average inter-stop distance (exclude depot legs)
    inter_stop_legs = leg_distances_miles[1:-1] if n_stops > 1 else []
    avg_inter_stop = sum(inter_stop_legs) / len(inter_stop_legs) if inter_stop_legs else 0.0

    return {
        "total_distance_miles": total_distance_miles,
        "leg_distances_miles": leg_distances_miles,
        "avg_inter_stop_distance_miles": avg_inter_stop,
    }


def get_route_matrix(
    route: Route,
    matrix_provider: MatrixProvider,
    work_rules: WorkRules | None = None,
) -> MatrixResult:
    """Build coordinate list and query the matrix provider for a route.

    Returns a square (n+1)x(n+1) matrix where:
    - index 0 = depot
    - indices 1..n = stops in sequence order

    When `matrix_provider` applies an active traffic profile and `work_rules` are
    given, this takes two passes: the first gets free-flow times, which are used
    to estimate when each stop is left, and the second re-requests the matrix
    banded by those departure times. Providers memoize the underlying fetch, so
    the second pass costs no extra network call.

    Providers without a traffic profile take the single-pass path unchanged.
    """
    coords: list[tuple[float, float]] = [(route.depot_lat, route.depot_lon)]
    for stop in route.stops:
        coords.append((stop.latitude, stop.longitude))

    # A live-traffic engine grades against the traffic the plan would actually
    # face, so give it the plan's start time rather than letting it default to
    # "now". Time-agnostic engines (OSRM, haversine) ignore it — and are not
    # handed it, so their cache is not fragmented by a value that never changes
    # their answer. Passed on BOTH passes below so the banded second pass shares
    # the memo/cache key with the free-flow first pass.
    depart = route.planned_start_time if getattr(matrix_provider, "is_time_aware", False) else None

    free_flow = matrix_provider.get_matrix(coords, coords, departure_time=depart)

    # isinstance rather than a truthiness check: getattr on a mock or proxy
    # auto-creates a truthy attribute, which would drag callers that never asked
    # for banding down the two-pass path.
    profile = getattr(matrix_provider, "profile", None)
    banding = isinstance(profile, TrafficProfile) and profile.is_active

    if banding and work_rules is None:
        # Silently returning free-flow here would grade the plan on a clock the
        # report claims is banded, so say it loudly rather than quietly disagree.
        logger.warning(
            "traffic_profile_ignored_without_work_rules",
            route_id=route.route_id,
            provider=getattr(matrix_provider, "name", "unknown"),
        )

    if not banding or work_rules is None:
        return free_flow

    departures = compute_departure_schedule(route, free_flow, work_rules)
    return matrix_provider.get_matrix(
        coords, coords, departure_time=depart, origin_departure_times=departures
    )
