"""Compliance diagnosis: time-window violations and shift overruns.

Grades the plan on the same clock the benchmark uses — the schedule propagated
through the matrix in `analysis.scoring.time` — so a traffic profile moves these
findings rather than leaving them pinned to static CSV values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from routebench.analysis.scoring.time import compute_time_metrics
from routebench.analysis.tools import ApplicabilityResult
from routebench.core.config import TrafficProfile, WorkRules
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet
    from routebench.infra.matrix.base import MatrixResult

# Free-flow times understate congestion, so arrivals are optimistic and any
# violation we do find is a floor rather than an estimate. Say so when no
# profile is active; drop the hedge when one is.
_FREE_FLOW_CAVEAT = (
    "Travel times are free-flow estimates with no traffic profile applied, so this "
    "is a lower bound — congestion can only push arrivals later, never earlier."
)
_PROFILED_NOTE = "Travel times reflect the configured traffic profile."


def _clock_note(profile: TrafficProfile | None) -> str:
    if profile is not None and profile.is_active:
        return _PROFILED_NOTE
    return _FREE_FLOW_CAVEAT


class ComplianceAnalysis:
    """Detects stops reached outside their time window and shifts running long."""

    name: str = "analyze_compliance"
    description: str = "Identify time-window violations and shift overruns"
    requires_matrix: bool = True

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        return ApplicabilityResult(
            is_applicable=True,
            reason="Shift rules apply to every route; time windows checked where present",
        )

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        matrices: dict[str, MatrixResult] = kwargs.get("matrices", {})  # type: ignore[assignment]
        work_rules: WorkRules = kwargs.get("work_rules", WorkRules())  # type: ignore[assignment]
        profile: TrafficProfile | None = kwargs.get("traffic_profile")  # type: ignore[assignment]
        route_metrics: dict[str, dict[str, object]] = kwargs.get("route_time_metrics", {})  # type: ignore[assignment]

        clock_note = _clock_note(profile)
        findings: list[Finding] = []

        for route in fleet.routes:
            matrix = matrices.get(route.route_id)
            if matrix is None:
                continue

            if route.route_id in route_metrics:
                time_met = route_metrics[route.route_id]
            else:
                time_met = compute_time_metrics(route, matrix, work_rules)

            findings.extend(self._time_window_findings(route.route_id, time_met, clock_note))
            findings.extend(
                self._shift_overrun_findings(route.route_id, time_met, work_rules, clock_note)
            )

        return findings

    def _time_window_findings(
        self,
        route_id: str,
        time_met: dict[str, object],
        clock_note: str,
    ) -> list[Finding]:
        raw = time_met.get("time_window_violations", 0)
        violations = int(raw) if isinstance(raw, (int, float)) else 0
        if violations < 1:
            return []

        severity = "high" if violations >= 3 else "medium"
        return [
            Finding(
                category="compliance",
                severity=severity,  # type: ignore[arg-type]
                confidence=0.90,
                title=(
                    f"Route {route_id}: {violations} stop(s) reached after their time window closes"
                ),
                evidence=[
                    FindingEvidence(
                        metric_name="time_window_violations",
                        actual_value=float(violations),
                        comparison_value=0.0,
                        comparison_type="threshold",
                        unit="stops",
                    ),
                ],
                references=FindingReference(route_ids=[route_id]),
                hypothesis=(
                    f"Route {route_id} cannot serve {violations} stop(s) within their "
                    f"committed windows as sequenced. {clock_note}"
                ),
                suggested_investigation=(
                    "Confirm the committed windows, then test whether resequencing or "
                    "moving the affected stops to another route restores feasibility"
                ),
            )
        ]

    def _shift_overrun_findings(
        self,
        route_id: str,
        time_met: dict[str, object],
        work_rules: WorkRules,
        clock_note: str,
    ) -> list[Finding]:
        raw = time_met.get("shift_overrun_minutes", 0.0)
        overrun = float(raw) if isinstance(raw, (int, float)) else 0.0
        if overrun <= 0:
            return []

        severity: str
        if overrun > 60:
            severity = "high"
        elif overrun > 15:
            severity = "medium"
        else:
            severity = "low"

        return [
            Finding(
                category="compliance",
                severity=severity,  # type: ignore[arg-type]
                confidence=0.90,
                title=f"Route {route_id}: shift runs {overrun:.0f} min over the cap",
                evidence=[
                    FindingEvidence(
                        metric_name="shift_overrun_minutes",
                        actual_value=overrun,
                        comparison_value=work_rules.max_shift_hours * 60.0,
                        comparison_type="threshold",
                        unit="minutes",
                    ),
                ],
                references=FindingReference(route_ids=[route_id]),
                hypothesis=(
                    f"Route {route_id} exceeds the {work_rules.max_shift_hours:.1f}h shift "
                    f"cap by {overrun:.0f} minutes as planned. {clock_note}"
                ),
                suggested_investigation=(
                    "Check whether the overrun is driven by drive time, service time, or "
                    "time-window idle, and whether stops can shed to a shorter route"
                ),
            )
        ]
