"""Density scoring: stops per hour, stops per mile, convex hull, compactness."""

from __future__ import annotations

import math

from routebench.core.schemas import Route

EARTH_RADIUS_MILES = 3958.8


def compute_density_metrics(
    route: Route,
    distance_metrics: dict[str, object],
    time_metrics: dict[str, object],
) -> dict[str, object]:
    """Compute density metrics for a single route.

    Args:
        route: The route with stops.
        distance_metrics: Output of compute_distance_metrics.
        time_metrics: Output of compute_time_metrics.

    Returns dict with:
    - stops_per_hour, stops_per_mile
    - avg_inter_stop_distance_miles
    - convex_hull_area_sq_miles
    - compactness_ratio
    """
    n_stops = len(route.stops)
    raw_dist = distance_metrics.get("total_distance_miles", 0.0)
    total_distance: float = float(raw_dist) if isinstance(raw_dist, (int, float)) else 0.0
    raw_time = time_metrics.get("total_time_hours", 0.0)
    total_time: float = float(raw_time) if isinstance(raw_time, (int, float)) else 0.0

    stops_per_hour = n_stops / total_time if total_time > 0 else 0.0
    stops_per_mile = n_stops / total_distance if total_distance > 0 else 0.0

    raw_avg = distance_metrics.get("avg_inter_stop_distance_miles", 0.0)
    avg_inter_stop: float = float(raw_avg) if isinstance(raw_avg, (int, float)) else 0.0

    # Convex hull area in square miles
    coords = [(s.latitude, s.longitude) for s in route.stops]
    hull_area = _convex_hull_area_sq_miles(coords)

    # Compactness ratio: hull_area / total_distance^2
    compactness = hull_area / (total_distance**2) if total_distance > 0 else 0.0

    return {
        "stops_per_hour": stops_per_hour,
        "stops_per_mile": stops_per_mile,
        "avg_inter_stop_distance_miles": avg_inter_stop,
        "convex_hull_area_sq_miles": hull_area,
        "compactness_ratio": compactness,
    }


def _convex_hull_area_sq_miles(coords: list[tuple[float, float]]) -> float:
    """Compute convex hull area in square miles using the shoelace formula.

    Converts lat/lon to approximate planar coordinates (miles) centered on centroid,
    then computes convex hull via Andrew's monotone chain + shoelace area.
    """
    if len(coords) < 3:
        return 0.0

    # Convert to planar miles centered on centroid
    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)

    miles_per_deg_lat = EARTH_RADIUS_MILES * math.pi / 180.0
    miles_per_deg_lon = miles_per_deg_lat * math.cos(math.radians(cx))

    points = [
        ((lat - cx) * miles_per_deg_lat, (lon - cy) * miles_per_deg_lon) for lat, lon in coords
    ]

    hull = _convex_hull(points)
    if len(hull) < 3:
        return 0.0

    return _shoelace_area(hull)


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain algorithm for 2D convex hull."""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def _shoelace_area(hull: list[tuple[float, float]]) -> float:
    """Shoelace formula for polygon area."""
    n = len(hull)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += hull[i][0] * hull[j][1]
        area -= hull[j][0] * hull[i][1]
    return abs(area) / 2.0
