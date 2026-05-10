"""Time pressure diagnosis: detect idle time patterns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from routebench.analysis.scoring.time import compute_time_metrics
from routebench.analysis.tools import ApplicabilityResult
from routebench.core.config import WorkRules
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet
    from routebench.infra.matrix.base import MatrixResult


class TimePressureAnalysis:
    """Detects significant idle time and classifies its cause."""

    name: str = "analyze_time_pressure"
    description: str = "Identify routes with significant idle time"
    requires_matrix: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        return ApplicabilityResult(
            is_applicable=True,
            reason="Checks all routes for idle time patterns",
        )

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        work_rules: WorkRules = kwargs.get("work_rules", WorkRules())  # type: ignore[assignment]
        route_metrics: dict[str, dict[str, object]] = kwargs.get("route_time_metrics", {})  # type: ignore[assignment]
        findings: list[Finding] = []

        for route in fleet.routes:
            matrix = matrices.get(route.route_id)
            if matrix is None:
                continue

            # Get time metrics if not pre-computed
            if route.route_id in route_metrics:
                time_met = route_metrics[route.route_id]
            else:
                time_met = compute_time_metrics(route, matrix, work_rules)

            idle_hours = time_met.get("idle_time_hours", 0.0)
            if not isinstance(idle_hours, (int, float)):
                idle_hours = 0.0

            if idle_hours < 0.5:
                continue

            total_hours = time_met.get("total_time_hours", 0.0)
            if not isinstance(total_hours, (int, float)):
                total_hours = 0.0

            idle_pct = (idle_hours / total_hours * 100) if total_hours > 0 else 0.0

            # Classify idle pattern
            has_time_windows = any(
                s.time_window_start is not None for s in route.stops
            )

            if has_time_windows:
                hypothesis = (
                    f"Route {route.route_id}: idle time driven by time windows"
                )
            elif idle_hours > 1.0:
                hypothesis = (
                    f"Route {route.route_id}: scattered idle time suggests "
                    f"service time underestimation"
                )
            else:
                hypothesis = (
                    f"Route {route.route_id}: moderate idle time, "
                    f"possible early return"
                )

            severity: str
            if idle_hours >= 2.0:
                severity = "high"
            elif idle_hours >= 1.0:
                severity = "medium"
            else:
                severity = "low"

            findings.append(
                Finding(
                    category="time_pressure",
                    severity=severity,  # type: ignore[arg-type]
                    confidence=0.75,
                    title=(
                        f"Route {route.route_id}: "
                        f"{idle_hours:.1f}h idle ({idle_pct:.0f}% of shift)"
                    ),
                    evidence=[
                        FindingEvidence(
                            metric_name="idle_time_hours",
                            actual_value=float(idle_hours),
                            comparison_value=0.5,
                            comparison_type="threshold",
                            unit="hours",
                        ),
                    ],
                    references=FindingReference(route_ids=[route.route_id]),
                    hypothesis=hypothesis,
                    suggested_investigation=(
                        "Review time window constraints and service time estimates"
                    ),
                )
            )

        return findings
