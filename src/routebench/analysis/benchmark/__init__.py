"""Benchmark package — route and fleet benchmark tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from routebench.analysis.benchmark.compare import (
    compute_fleet_benchmark,
    compute_route_benchmark,
)
from routebench.analysis.benchmark.tsptw import solve_tsptw
from routebench.analysis.benchmark.vrptw import solve_vrptw
from routebench.analysis.tools import ApplicabilityResult
from routebench.core.config import WorkRules
from routebench.core.findings import (
    Finding,
    FindingEvidence,
    FindingReference,
)

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet, Stop
    from routebench.infra.matrix.base import MatrixResult


class RouteBenchmarkTool:
    """Runs TSPTW per route, produces findings for routes with >5% gap."""

    name: str = "route_benchmark"
    description: str = "Benchmark each route against optimal TSPTW solution"
    requires_matrix: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        return ApplicabilityResult(is_applicable=True, reason="Always applicable")

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        work_rules: WorkRules = kwargs.get("work_rules", WorkRules())  # type: ignore[assignment]
        time_limit: int = kwargs.get("time_limit_s", 30)  # type: ignore[assignment]
        findings: list[Finding] = []

        for route in fleet.routes:
            matrix = matrices.get(route.route_id)
            if matrix is None:
                continue

            optimal = solve_tsptw(route, matrix, work_rules, time_limit_s=time_limit)
            benchmark = compute_route_benchmark(route, optimal, matrix)

            if benchmark.distance_gap_pct > 5.0:
                severity = "high" if benchmark.distance_gap_pct > 20.0 else "medium"
                findings.append(
                    Finding(
                        category="sequencing",
                        severity=severity,  # type: ignore[arg-type]
                        confidence=0.90,
                        title=(
                            f"Route {route.route_id}: {benchmark.distance_gap_pct:.1f}% "
                            f"distance gap vs optimal"
                        ),
                        evidence=[
                            FindingEvidence(
                                metric_name="distance_gap_pct",
                                actual_value=benchmark.distance_gap_pct,
                                comparison_value=5.0,
                                comparison_type="optimal",
                                unit="percent",
                            ),
                        ],
                        references=FindingReference(
                            route_ids=[route.route_id],
                        ),
                        hypothesis=(
                            f"Route could save {benchmark.distance_gap_pct:.1f}% distance "
                            f"with optimal sequencing"
                        ),
                        suggested_investigation="Run resequencing with OR-Tools solution",
                    )
                )

        return findings


class FleetBenchmarkTool:
    """Runs VRPTW once, produces fleet-level and migration findings."""

    name: str = "fleet_benchmark"
    description: str = "Benchmark fleet against optimal VRPTW solution"
    requires_matrix: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        return ApplicabilityResult(is_applicable=True, reason="Always applicable")

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        combined_matrix: MatrixResult | None = kwargs.get("combined_matrix")  # type: ignore[assignment]
        work_rules: WorkRules = kwargs.get("work_rules", WorkRules())  # type: ignore[assignment]
        time_limit: int = kwargs.get("time_limit_s", 120)  # type: ignore[assignment]
        findings: list[Finding] = []

        if combined_matrix is None:
            return findings

        all_stops: list[Stop] = []
        for route in fleet.routes:
            all_stops.extend(route.stops)

        solution = solve_vrptw(fleet, combined_matrix, work_rules, time_limit_s=time_limit)
        benchmark = compute_fleet_benchmark(fleet, solution, all_stops)

        if benchmark.optimality_gap_reported_by_solver > 0.05:
            findings.append(
                Finding(
                    category="dispatch",
                    severity="medium",
                    confidence=0.85,
                    title=(
                        f"Fleet-level optimization gap: "
                        f"{benchmark.optimality_gap_reported_by_solver:.1%}"
                    ),
                    evidence=[
                        FindingEvidence(
                            metric_name="fleet_optimality_gap",
                            actual_value=benchmark.optimality_gap_reported_by_solver * 100,
                            comparison_value=5.0,
                            comparison_type="optimal",
                            unit="percent",
                        ),
                    ],
                    references=FindingReference(
                        route_ids=[r.route_id for r in fleet.routes],
                    ),
                    hypothesis="Fleet routing can be improved with cross-route optimization",
                    suggested_investigation="Review stop-to-route assignments",
                )
            )

        if benchmark.stop_migrations:
            n_migrations = len(benchmark.stop_migrations)
            findings.append(
                Finding(
                    category="territory",
                    severity="medium" if n_migrations > 3 else "low",
                    confidence=0.80,
                    title=f"{n_migrations} stops would migrate in optimal solution",
                    evidence=[
                        FindingEvidence(
                            metric_name="stop_migrations",
                            actual_value=float(n_migrations),
                            comparison_value=0.0,
                            comparison_type="optimal",
                            unit="stops",
                        ),
                    ],
                    references=FindingReference(
                        route_ids=list(
                            {m.from_route for m in benchmark.stop_migrations}
                            | {m.to_route for m in benchmark.stop_migrations}
                        ),
                    ),
                    hypothesis="Some stops are assigned to non-optimal routes",
                    suggested_investigation=("Review stop migrations for territory re-balancing"),
                )
            )

        return findings
