"""Outlier diagnosis: detect outlier stops and multi-dimensional outlier routes."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from routebench.analysis.tools import ApplicabilityResult
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet
    from routebench.infra.matrix.base import MatrixResult


class OutlierAnalysis:
    """Detects outlier stops and multi-dimensional outlier routes."""

    name: str = "analyze_outliers"
    description: str = "Identify outlier stops and routes"
    requires_matrix: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        has_enough = any(len(r.stops) >= 5 for r in fleet.routes)
        if has_enough:
            return ApplicabilityResult(
                is_applicable=True,
                reason="At least one route has ≥5 stops",
            )
        return ApplicabilityResult(
            is_applicable=False,
            reason="All routes have <5 stops",
        )

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        findings: list[Finding] = []

        for route in fleet.routes:
            if len(route.stops) < 5:
                continue

            matrix = matrices.get(route.route_id)
            if matrix is None:
                continue

            distances = matrix.distances_array()
            n = len(route.stops)

            # Compute nearest-neighbor distance for each stop (within route)
            nn_dists: list[float] = []
            for i in range(1, n + 1):
                min_d = float("inf")
                for j in range(1, n + 1):
                    if i != j:
                        d = float(distances[i, j])
                        if d < min_d:
                            min_d = d
                nn_dists.append(min_d)

            if not nn_dists:
                continue

            median_nn = statistics.median(nn_dists)
            if median_nn <= 0:
                continue

            threshold = 1.5 * median_nn

            for idx, nn_dist in enumerate(nn_dists):
                if nn_dist > threshold:
                    stop = route.stops[idx]
                    ratio = nn_dist / median_nn
                    findings.append(
                        Finding(
                            category="outlier",
                            severity="medium" if ratio > 2.0 else "low",
                            confidence=0.80,
                            title=(
                                f"Route {route.route_id}, stop {stop.stop_sequence}: "
                                f"outlier ({ratio:.1f}x median NN distance)"
                            ),
                            evidence=[
                                FindingEvidence(
                                    metric_name="nearest_neighbor_distance",
                                    actual_value=nn_dist,
                                    comparison_value=median_nn,
                                    comparison_type="fleet_median",
                                    unit="meters",
                                ),
                            ],
                            references=FindingReference(
                                route_ids=[route.route_id],
                                stop_sequences=[(route.route_id, stop.stop_sequence)],
                            ),
                            hypothesis=(
                                f"Stop {stop.stop_sequence} is geographically "
                                f"distant from other stops on this route"
                            ),
                            suggested_investigation=(
                                "Consider reassigning this stop to a geographically closer route"
                            ),
                        )
                    )

        return findings
