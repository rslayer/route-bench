"""Sequencing diagnosis: detect suboptimal stop ordering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from routebench.analysis.tools import ApplicabilityResult
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet
    from routebench.infra.matrix.base import MatrixResult


class SequencingAnalysis:
    """Detects sequencing inefficiency in routes."""

    name: str = "analyze_sequencing"
    description: str = "Identify routes with suboptimal stop ordering"
    requires_matrix: bool = True

    def __init__(self, threshold: float = 1.30) -> None:
        self._threshold = threshold

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        return ApplicabilityResult(is_applicable=True, reason="Always applicable")

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        findings: list[Finding] = []

        for route in fleet.routes:
            if len(route.stops) < 2:
                continue

            matrix = matrices.get(route.route_id)
            if matrix is None:
                continue

            distances = matrix.distances_array()
            n = len(route.stops)

            # Actual distance: depot→s1→s2→...→sN→depot
            actual = 0.0
            actual += float(distances[0, 1])
            for i in range(1, n):
                actual += float(distances[i, i + 1])
            actual += float(distances[n, 0])

            # Nearest-neighbor heuristic distance
            nn_dist = _nearest_neighbor_distance(distances, n)

            if nn_dist <= 0:
                continue

            seq_index = actual / nn_dist

            if seq_index < self._threshold:
                continue

            # Find worst leg
            worst_leg_idx, _worst_deviation = _find_worst_leg(distances, n)

            # Detect crossings
            coords = [(route.depot_lat, route.depot_lon)]
            for s in route.stops:
                coords.append((s.latitude, s.longitude))
            crossings = _detect_crossings(coords)

            # Build hypothesis
            if crossings:
                hypothesis = (
                    "Geographic crossing suggests sub-optimal sequencing"
                )
            else:
                hypothesis = "Sequencing inefficiency"

            # Severity
            if seq_index >= 1.40:
                severity = "high"
            elif seq_index >= 1.25:
                severity = "medium"
            else:
                severity = "low"

            stop_refs: list[tuple[str, int]] = []
            if worst_leg_idx is not None and 1 <= worst_leg_idx <= n:
                stop_refs.append((route.route_id, worst_leg_idx))
            if worst_leg_idx is not None and worst_leg_idx + 1 <= n:
                stop_refs.append((route.route_id, worst_leg_idx + 1))

            findings.append(
                Finding(
                    category="sequencing",
                    severity=severity,  # type: ignore[arg-type]
                    confidence=0.85,
                    title=f"Route {route.route_id}: sequencing index {seq_index:.2f}",
                    evidence=[
                        FindingEvidence(
                            metric_name="sequencing_index",
                            actual_value=seq_index,
                            comparison_value=self._threshold,
                            comparison_type="threshold",
                            unit="ratio",
                        ),
                    ],
                    references=FindingReference(
                        route_ids=[route.route_id],
                        stop_sequences=stop_refs,
                    ),
                    hypothesis=hypothesis,
                    suggested_investigation=(
                        "Review stop ordering for potential resequencing"
                    ),
                )
            )

        return findings


def _nearest_neighbor_distance(
    distances: object, n_stops: int
) -> float:
    """Compute nearest-neighbor heuristic tour distance.

    Starts at depot (index 0), greedily visits nearest unvisited stop,
    returns to depot.
    """
    import numpy as np

    dist = np.asarray(distances, dtype=float)
    visited = [False] * (n_stops + 1)
    visited[0] = True
    current = 0
    total = 0.0

    for _ in range(n_stops):
        best_next = -1
        best_dist = float("inf")
        for j in range(1, n_stops + 1):
            if not visited[j] and dist[current, j] < best_dist:
                best_dist = float(dist[current, j])
                best_next = j
        if best_next < 0:
            break
        visited[best_next] = True
        total += best_dist
        current = best_next

    total += float(dist[current, 0])
    return total


def _find_worst_leg(
    distances: object, n_stops: int
) -> tuple[int | None, float]:
    """Find the leg with largest deviation from a direct path."""
    import numpy as np

    dist = np.asarray(distances, dtype=float)

    worst_idx: int | None = None
    worst_val = 0.0

    # Check depot→s1
    if n_stops >= 1 and dist[0, 1] > worst_val:
        worst_val = float(dist[0, 1])
        worst_idx = 0

    # Check stop-to-stop legs
    for i in range(1, n_stops):
        val = float(dist[i, i + 1])
        if val > worst_val:
            worst_val = val
            worst_idx = i

    # Check last→depot
    if n_stops >= 1 and dist[n_stops, 0] > worst_val:
        worst_val = float(dist[n_stops, 0])
        worst_idx = n_stops

    return worst_idx, worst_val


def _detect_crossings(
    coords: list[tuple[float, float]],
) -> list[tuple[int, int]]:
    """Detect crossing legs in the route (treat lat/lon as planar).

    Returns list of (leg_i, leg_j) pairs that intersect.
    """
    n = len(coords)
    if n < 4:
        return []

    # Build leg segments: (i, i+1) for i in 0..n-2, plus (n-1, 0) for return
    legs: list[tuple[int, int]] = []
    for i in range(n - 1):
        legs.append((i, i + 1))
    legs.append((n - 1, 0))

    crossings: list[tuple[int, int]] = []
    for i in range(len(legs)):
        for j in range(i + 2, len(legs)):
            if i == 0 and j == len(legs) - 1:
                continue  # adjacent legs share depot
            a1, a2 = legs[i]
            b1, b2 = legs[j]
            if _segments_intersect(
                coords[a1], coords[a2], coords[b1], coords[b2]
            ):
                crossings.append((i, j))

    return crossings


def _segments_intersect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    """Check if line segments (p1,p2) and (p3,p4) intersect (proper intersection)."""
    d1 = _cross_product(p3, p4, p1)
    d2 = _cross_product(p3, p4, p2)
    d3 = _cross_product(p1, p2, p3)
    d4 = _cross_product(p1, p2, p4)

    return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    )


def _cross_product(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    """2D cross product of vectors (b-a) and (c-a)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
