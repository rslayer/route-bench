"""Reachability diagnosis: flag routes whose planned sequence cannot be driven.

When the routing engine returns no road route between two consecutive planned
stops, that leg's travel time is infinite: the plan, as sequenced, is not
drivable. Grading already handles this without crashing (the affected fleet
dimension is simply not scored), but silence is the wrong answer for a
benchmarking tool — the user should be told their plan has an unreachable leg,
because it usually means a bad coordinate (a stop dropped in water or off-road)
or a genuinely disconnected location, not a routing quirk.

The planned legs follow the scorecard's matrix convention (see
analysis/scoring/time.py): index 0 is the depot, 1..n the stops in order, so the
driven legs are depot->first, each stop->next, and last->depot.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from routebench.analysis.tools import ApplicabilityResult
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet, Route
    from routebench.infra.matrix.base import MatrixResult


def _planned_legs(n_stops: int) -> list[tuple[int, int]]:
    """Matrix (from, to) index pairs for the driven sequence, incl. depot legs."""
    if n_stops < 1:
        return []
    legs = [(0, 1)]  # depot -> first stop
    legs += [(i, i + 1) for i in range(1, n_stops)]  # stop -> next
    legs.append((n_stops, 0))  # last stop -> depot
    return legs


class ReachabilityAnalysis:
    """Flags routes with an unreachable (infinite-duration) planned leg."""

    name: str = "analyze_reachability"
    description: str = "Detect routes whose planned sequence has an unreachable leg"
    requires_matrix: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        if any(len(r.stops) >= 1 for r in fleet.routes):
            return ApplicabilityResult(is_applicable=True, reason="Fleet has routes to check")
        return ApplicabilityResult(is_applicable=False, reason="No routes with stops")

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        findings: list[Finding] = []

        for route in fleet.routes:
            matrix = matrices.get(route.route_id)
            if matrix is None:
                continue
            finding = self._check_route(route, matrix)
            if finding is not None:
                findings.append(finding)

        return findings

    def _check_route(self, route: Route, matrix: MatrixResult) -> Finding | None:
        n_stops = len(route.stops)
        durations = matrix.durations_seconds
        dim = len(durations)
        n_unreachable = 0
        for a, b in _planned_legs(n_stops):
            # Defend against a matrix smaller than the route (should not happen,
            # but a malformed matrix must not raise here).
            if a >= dim or b >= len(durations[a]):
                continue
            if not math.isfinite(float(durations[a][b])):
                n_unreachable += 1

        if n_unreachable == 0:
            return None

        return Finding(
            category="reachability",
            severity="high",
            confidence=1.0,
            title=f"Route {route.route_id} has an unreachable leg",
            evidence=[
                FindingEvidence(
                    metric_name="unreachable_legs",
                    actual_value=float(n_unreachable),
                    unit="legs",
                )
            ],
            references=FindingReference(route_ids=[route.route_id]),
            hypothesis=(
                f"{n_unreachable} leg(s) in this route have no drivable road route, so the plan "
                "as sequenced cannot be completed. This is usually a bad stop coordinate — a point "
                "dropped off-road, in water, or across an uncrossable barrier — rather than a "
                "routing error. Travel-time metrics and the fleet benchmark treat this route as "
                "infeasible and do not score it."
            ),
            suggested_investigation=(
                "Check the coordinates of the stops on this route for one that is far from a road "
                "or clearly misplaced, and correct or remove it."
            ),
        )
