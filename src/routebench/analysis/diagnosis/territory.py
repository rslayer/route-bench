"""Territory diagnosis: detect depot stress and geographic overlap."""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

from routebench.analysis.tools import ApplicabilityResult
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet

METERS_PER_MILE = 1609.34
EARTH_RADIUS_MILES = 3958.8


class TerritoryAnalysis:
    """Detects territory misalignment: depot stress and geographic overlap."""

    name: str = "analyze_territory"
    description: str = "Identify territory misalignment between routes"
    requires_matrix: bool = False

    def __init__(self, depot_stress_miles: float = 15.0) -> None:
        self._depot_stress_miles = depot_stress_miles

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        if len(fleet.routes) >= 2:
            return ApplicabilityResult(
                is_applicable=True,
                reason="Fleet has ≥2 routes",
            )
        return ApplicabilityResult(
            is_applicable=False,
            reason="Fleet has <2 routes",
        )

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        findings: list[Finding] = []

        # Depot stress
        depot_distances: list[float] = []
        for route in fleet.routes:
            if not route.stops:
                continue
            centroid_lat = sum(s.latitude for s in route.stops) / len(route.stops)
            centroid_lon = sum(s.longitude for s in route.stops) / len(route.stops)
            dist = _haversine_miles(route.depot_lat, route.depot_lon, centroid_lat, centroid_lon)
            depot_distances.append(dist)

        if depot_distances:
            median_depot_dist = statistics.median(depot_distances)
            if median_depot_dist > self._depot_stress_miles:
                findings.append(
                    Finding(
                        category="territory",
                        severity="medium",
                        confidence=0.70,
                        title=(
                            f"Depot stress: median distance to route centroids "
                            f"is {median_depot_dist:.1f} miles"
                        ),
                        evidence=[
                            FindingEvidence(
                                metric_name="median_depot_to_centroid_miles",
                                actual_value=median_depot_dist,
                                comparison_value=self._depot_stress_miles,
                                comparison_type="threshold",
                                unit="miles",
                            ),
                        ],
                        references=FindingReference(
                            route_ids=[r.route_id for r in fleet.routes],
                        ),
                        hypothesis=(
                            "Depot location may not be optimal for the current route distribution"
                        ),
                        suggested_investigation=(
                            "Evaluate depot placement relative to delivery clusters"
                        ),
                    )
                )

        # Geographic overlap: check each pair of routes
        route_hulls: dict[str, list[tuple[float, float]]] = {}
        for route in fleet.routes:
            if len(route.stops) >= 3:
                coords = [(s.latitude, s.longitude) for s in route.stops]
                hull = _convex_hull(coords)
                if len(hull) >= 3:
                    route_hulls[route.route_id] = hull

        checked: set[tuple[str, str]] = set()
        for rid_a, hull_a in route_hulls.items():
            for rid_b, hull_b in route_hulls.items():
                if rid_a >= rid_b:
                    continue
                pair = (rid_a, rid_b)
                if pair in checked:
                    continue
                checked.add(pair)

                # Find the routes
                route_a = next(r for r in fleet.routes if r.route_id == rid_a)
                route_b = next(r for r in fleet.routes if r.route_id == rid_b)

                # Fraction of A's stops inside B's hull
                a_in_b = sum(
                    1 for s in route_a.stops if _point_in_polygon((s.latitude, s.longitude), hull_b)
                ) / max(len(route_a.stops), 1)

                # Fraction of B's stops inside A's hull
                b_in_a = sum(
                    1 for s in route_b.stops if _point_in_polygon((s.latitude, s.longitude), hull_a)
                ) / max(len(route_b.stops), 1)

                if a_in_b > 0.20 or b_in_a > 0.20:
                    overlap_pct = max(a_in_b, b_in_a) * 100
                    findings.append(
                        Finding(
                            category="territory",
                            severity="medium",
                            confidence=0.75,
                            title=(
                                f"Routes {rid_a} and {rid_b}: {overlap_pct:.0f}% geographic overlap"
                            ),
                            evidence=[
                                FindingEvidence(
                                    metric_name="geographic_overlap_pct",
                                    actual_value=overlap_pct,
                                    comparison_value=20.0,
                                    comparison_type="threshold",
                                    unit="percent",
                                ),
                            ],
                            references=FindingReference(
                                route_ids=[rid_a, rid_b],
                            ),
                            hypothesis=(
                                "Territory misalignment: routes serve overlapping geographic areas"
                            ),
                            suggested_investigation=(
                                "Consider territory re-balancing to reduce overlap"
                            ),
                        )
                    )

        return findings


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two lat/lon points in miles."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_MILES * c


def _convex_hull(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Andrew's monotone chain for convex hull."""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(
        o: tuple[float, float],
        a: tuple[float, float],
        b: tuple[float, float],
    ) -> float:
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


def _point_in_polygon(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> bool:
    """Ray casting algorithm for point-in-polygon test."""
    x, y = point
    n = len(polygon)
    inside = False

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i

    return inside
