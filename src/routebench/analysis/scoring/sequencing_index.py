"""Sequencing index: actual tour distance / nearest-neighbor heuristic distance."""

from __future__ import annotations

from routebench.core.schemas import Route
from routebench.infra.matrix.base import MatrixResult


def compute_sequencing_index(route: Route, matrix: MatrixResult) -> float | None:
    """Compute the sequencing index for a single route.

    sequencing_index = actual_distance / nn_heuristic_distance.
    Returns None if the route has fewer than 2 stops or NN distance is zero.
    """
    n = len(route.stops)
    if n < 2:
        return None

    dist = matrix.distances_array()

    # Actual distance: depot(0)→s1(1)→s2(2)→...→sN(n)→depot(0)
    actual = float(dist[0, 1])
    for i in range(1, n):
        actual += float(dist[i, i + 1])
    actual += float(dist[n, 0])

    # Nearest-neighbor heuristic
    nn_dist = _nearest_neighbor_distance(dist, n)

    if nn_dist <= 0:
        return None

    return actual / nn_dist


def _nearest_neighbor_distance(
    distances: object,
    n_stops: int,
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
            if not visited[j] and float(dist[current, j]) < best_dist:
                best_dist = float(dist[current, j])
                best_next = j
        if best_next < 0:
            break
        visited[best_next] = True
        total += best_dist
        current = best_next

    total += float(dist[current, 0])
    return total
