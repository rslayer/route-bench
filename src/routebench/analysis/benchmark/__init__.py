"""Benchmark package — route and fleet benchmark tools."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from routebench.analysis.benchmark.budget import fleet_time_limit_s, route_time_limit_s
from routebench.analysis.benchmark.compare import (
    compute_fleet_benchmark,
    compute_route_benchmark,
)
from routebench.analysis.benchmark.fleet_matrix import (
    MAX_FLEET_BENCHMARK_STOPS,
    MAX_ROUTE_BENCHMARK_STOPS,
    fleet_depot,
)
from routebench.analysis.benchmark.tsptw import solve_tsptw
from routebench.analysis.benchmark.vrptw import solve_vrptw
from routebench.analysis.tools import ApplicabilityResult
from routebench.core.config import AnalysisConfig, WorkRules
from routebench.core.findings import (
    Finding,
    FindingEvidence,
    FindingReference,
    RouteBenchmark,
)

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet, Route, Stop
    from routebench.infra.matrix.base import MatrixResult


class RouteBenchmarkTool:
    """Runs TSPTW per route, produces findings for routes with >5% gap."""

    name: str = "route_benchmark"
    description: str = "Benchmark each route against optimal TSPTW solution"
    requires_matrix: bool = True
    is_benchmark: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        # No route-count cap: each route solves independently, so route count
        # only affects total wall-clock, which the shared solver envelope and
        # parallel workers already bound (see route_time_limit_s). Only the
        # fleet-total stop count is a real ceiling here.
        n_stops = fleet.total_stops()
        if n_stops > MAX_ROUTE_BENCHMARK_STOPS:
            return ApplicabilityResult(
                is_applicable=False,
                reason=(
                    f"{n_stops:,} stops exceeds the {MAX_ROUTE_BENCHMARK_STOPS:,}-stop cap "
                    f"for per-route re-solve; analysing descriptively"
                ),
            )
        return ApplicabilityResult(is_applicable=True, reason="within the per-route solve budget")

    @staticmethod
    def _solve_routes(
        routes: list[Route],
        matrices: dict[str, MatrixResult],
        work_rules: WorkRules,
        time_limit: int,
        config: AnalysisConfig | None,
    ) -> list[tuple[Route, RouteBenchmark]]:
        """Solve every route's optimal TSPTW, in parallel.

        Routes are independent problems, so they solve concurrently — OR-Tools
        releases the GIL during search, so a thread pool gives real parallelism
        and the fleet's wall-clock is roughly the slowest batch, not the sum.
        Each route's time limit is divided out of the shared solver envelope by
        route_time_limit_s. Order is preserved so findings are deterministic.
        """
        n = len(routes)
        if n == 0:
            return []

        def solve_one(route: Route) -> tuple[Route, RouteBenchmark]:
            matrix = matrices[route.route_id]
            limit = route_time_limit_s(len(route.stops), config, n) if config else time_limit
            optimal = solve_tsptw(route, matrix, work_rules, time_limit_s=limit)
            return route, compute_route_benchmark(route, optimal, matrix)

        workers = min(config.route_benchmark_workers if config else 1, n)
        if workers <= 1:
            return [solve_one(r) for r in routes]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(solve_one, routes))

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        work_rules: WorkRules = kwargs.get("work_rules", WorkRules())  # type: ignore[assignment]
        time_limit: int = kwargs.get("time_limit_s", 30)  # type: ignore[assignment]
        # When the orchestrator passes the config, each route's budget scales with
        # its own stop count AND the fleet's route count (shared envelope);
        # otherwise the flat time_limit_s applies (tests, and any caller that has
        # not opted in).
        config: AnalysisConfig | None = kwargs.get("analysis_config")  # type: ignore[assignment]
        # Solving is the expensive part; hand the structured result back through
        # the sink so the report can show it without paying for a second solve.
        sink: dict[str, object] | None = kwargs.get("benchmark_sink")  # type: ignore[assignment]
        findings: list[Finding] = []
        per_route: dict[str, RouteBenchmark] = {}

        solvable = [r for r in fleet.routes if matrices.get(r.route_id) is not None]
        for route, benchmark in self._solve_routes(
            solvable, matrices, work_rules, time_limit, config
        ):
            per_route[route.route_id] = benchmark

            if benchmark.distance_gap_pct > 5.0:
                severity = "high" if benchmark.distance_gap_pct > 20.0 else "medium"
                findings.append(
                    Finding(
                        category="sequencing",
                        severity=severity,  # type: ignore[arg-type]
                        confidence=0.90,
                        title=(
                            f"Route {route.route_id}: {benchmark.distance_gap_pct:.1f}% "
                            f"distance gap vs solver solution"
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
                            f"Route could save at least {benchmark.distance_gap_pct:.1f}% "
                            f"distance with better sequencing"
                        ),
                        suggested_investigation="Run resequencing with OR-Tools solution",
                    )
                )
            elif benchmark.distance_gap_pct <= 0.0:
                # The solver, given a time limit, found nothing better than the
                # plan. Reporting this is the point of a benchmark: a plan that
                # survives the check has earned a clean bill, not silence.
                findings.append(
                    Finding(
                        category="sequencing",
                        severity="info",
                        confidence=0.90,
                        title=(
                            f"Route {route.route_id}: plan is within solver reach — "
                            f"no material sequencing savings found"
                        ),
                        evidence=[
                            FindingEvidence(
                                metric_name="distance_gap_pct",
                                actual_value=benchmark.distance_gap_pct,
                                comparison_value=0.0,
                                comparison_type="optimal",
                                unit="percent",
                            ),
                        ],
                        references=FindingReference(
                            route_ids=[route.route_id],
                        ),
                        hypothesis=(
                            f"Route {route.route_id} is sequenced at least as well as the "
                            f"solver managed within its time limit; no resequencing "
                            f"opportunity was identified"
                        ),
                        suggested_investigation=(
                            "No action indicated for sequencing; look to other categories "
                            "for this route"
                        ),
                    )
                )

        if sink is not None:
            sink["per_route"] = per_route

        return findings


class FleetBenchmarkTool:
    """Runs VRPTW once, produces fleet-level and migration findings."""

    name: str = "fleet_benchmark"
    description: str = "Benchmark fleet against optimal VRPTW solution"
    requires_matrix: bool = True
    is_benchmark: bool = True
    # Signals the orchestrator to build the combined fleet matrix, which is a
    # large extra fetch and so is only built for tools that need it.
    requires_fleet_matrix: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        if len(fleet.routes) < 2:
            return ApplicabilityResult(
                is_applicable=False,
                reason="cross-route optimization needs at least 2 routes",
            )
        if fleet_depot(fleet) is None:
            return ApplicabilityResult(
                is_applicable=False,
                reason="routes do not share a single depot",
            )
        n_stops = fleet.total_stops()
        if n_stops > MAX_FLEET_BENCHMARK_STOPS:
            return ApplicabilityResult(
                is_applicable=False,
                reason=(
                    f"{n_stops} stops exceeds the {MAX_FLEET_BENCHMARK_STOPS}-stop "
                    f"cap for fleet-level VRPTW"
                ),
            )
        return ApplicabilityResult(
            is_applicable=True,
            reason=f"{len(fleet.routes)} routes share one depot",
        )

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        combined_matrix: MatrixResult | None = kwargs.get("combined_matrix")  # type: ignore[assignment]
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        work_rules: WorkRules = kwargs.get("work_rules", WorkRules())  # type: ignore[assignment]
        time_limit: int = kwargs.get("time_limit_s", 120)  # type: ignore[assignment]
        config: AnalysisConfig | None = kwargs.get("analysis_config")  # type: ignore[assignment]
        sink: dict[str, object] | None = kwargs.get("benchmark_sink")  # type: ignore[assignment]
        findings: list[Finding] = []

        if combined_matrix is None:
            return findings

        all_stops: list[Stop] = []
        for route in fleet.routes:
            all_stops.extend(route.stops)

        # Scale the fleet solve with total stops when the config is available, so
        # a small fleet is not held for the full ceiling.
        fleet_limit = fleet_time_limit_s(len(all_stops), config) if config else time_limit
        solution = solve_vrptw(fleet, combined_matrix, work_rules, time_limit_s=fleet_limit)
        # Without per_route_matrices the actual total is silently 0.0, which would
        # report the whole fleet's mileage as pure savings.
        benchmark = compute_fleet_benchmark(fleet, solution, all_stops, per_route_matrices=matrices)

        if sink is not None:
            sink["fleet_level"] = benchmark

        if benchmark.improvement_gap_pct > 5.0:
            findings.append(
                Finding(
                    category="dispatch",
                    severity="medium",
                    confidence=0.85,
                    title=(
                        f"Fleet-level improvement available: {benchmark.improvement_gap_pct:.1f}%"
                    ),
                    evidence=[
                        FindingEvidence(
                            metric_name="fleet_improvement_gap_pct",
                            actual_value=benchmark.improvement_gap_pct,
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
        elif benchmark.improvement_gap_pct <= 0.0 and not benchmark.stop_migrations:
            findings.append(
                Finding(
                    category="dispatch",
                    severity="info",
                    confidence=0.85,
                    title=(
                        "Fleet plan is within solver reach — no material cross-route savings found"
                    ),
                    evidence=[
                        FindingEvidence(
                            metric_name="fleet_improvement_gap_pct",
                            actual_value=benchmark.improvement_gap_pct,
                            comparison_value=0.0,
                            comparison_type="optimal",
                            unit="percent",
                        ),
                    ],
                    references=FindingReference(
                        route_ids=[r.route_id for r in fleet.routes],
                    ),
                    hypothesis=(
                        "Stop-to-route assignments are at least as good as the solver "
                        "managed within its time limit"
                    ),
                    suggested_investigation=("No action indicated for cross-route assignment"),
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
