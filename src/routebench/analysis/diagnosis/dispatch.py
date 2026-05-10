"""Dispatch diagnosis: detect rush-hour clustering of route start times."""

from __future__ import annotations

from typing import TYPE_CHECKING

from routebench.analysis.tools import ApplicabilityResult
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.schemas import Fleet


class DispatchAnalysis:
    """Detects rush-hour clustering of route start times."""

    name: str = "analyze_dispatch"
    description: str = "Identify clustered dispatch times"
    requires_matrix: bool = False

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        has_all = all(
            r.planned_start_time is not None for r in fleet.routes
        )
        if has_all and len(fleet.routes) >= 2:
            return ApplicabilityResult(
                is_applicable=True,
                reason="All routes have planned_start_time",
            )
        return ApplicabilityResult(
            is_applicable=False,
            reason="Not all routes have planned_start_time",
        )

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        findings: list[Finding] = []

        if len(fleet.routes) < 2:
            return findings

        # Extract start times as minutes since midnight
        start_minutes: list[float] = []
        for route in fleet.routes:
            t = route.planned_start_time
            start_minutes.append(t.hour * 60.0 + t.minute + t.second / 60.0)

        if not start_minutes:
            return findings

        # Find largest cluster within 15-minute window
        start_minutes.sort()
        n = len(start_minutes)
        max_cluster = 0
        cluster_start = 0.0

        for i in range(n):
            count = 0
            for j in range(n):
                if abs(start_minutes[j] - start_minutes[i]) <= 15.0:
                    count += 1
            if count > max_cluster:
                max_cluster = count
                cluster_start = start_minutes[i]

        cluster_pct = max_cluster / n

        if cluster_pct > 0.70:
            # Format the cluster time
            cluster_hour = int(cluster_start // 60)
            cluster_min = int(cluster_start % 60)
            time_str = f"{cluster_hour:02d}:{cluster_min:02d}"

            findings.append(
                Finding(
                    category="dispatch",
                    severity="medium",
                    confidence=0.80,
                    title=(
                        f"{max_cluster}/{n} routes ({cluster_pct:.0%}) "
                        f"start within 15 min of {time_str}"
                    ),
                    evidence=[
                        FindingEvidence(
                            metric_name="dispatch_cluster_pct",
                            actual_value=cluster_pct * 100,
                            comparison_value=70.0,
                            comparison_type="threshold",
                            unit="percent",
                        ),
                    ],
                    references=FindingReference(
                        route_ids=[r.route_id for r in fleet.routes],
                    ),
                    hypothesis=(
                        "Rush-hour clustering: most routes depart at the "
                        "same time, potentially causing dock congestion"
                    ),
                    suggested_investigation=(
                        "Consider staggering dispatch times to reduce "
                        "loading dock congestion"
                    ),
                )
            )

        return findings
