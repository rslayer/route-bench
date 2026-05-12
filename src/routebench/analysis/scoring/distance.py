"""Distance scoring: compute route distance metrics from matrix data."""

from __future__ import annotations

from routebench.core.schemas import Route
from routebench.infra.matrix.base import MatrixProvider, MatrixResult

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


def get_route_matrix(route: Route, matrix_provider: MatrixProvider) -> MatrixResult:
    """Build coordinate list and query the matrix provider for a route.

    Returns a square (n+1)x(n+1) matrix where:
    - index 0 = depot
    - indices 1..n = stops in sequence order
    """
    coords: list[tuple[float, float]] = [(route.depot_lat, route.depot_lon)]
    for stop in route.stops:
        coords.append((stop.latitude, stop.longitude))

    return matrix_provider.get_matrix(coords, coords)
