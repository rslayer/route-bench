"""Service-time sanity: flag stops whose dwell time is implausible for the vertical.

Once an industry profile is chosen it carries a plausible per-stop service-time
band (a courier drop is seconds-to-minutes; a big-and-bulky install is tens of
minutes to hours). A stop whose uploaded service time falls outside that band is
almost always a data problem — a units mix-up, a mis-keyed value, or the wrong
industry selected — not a real 45-minute courier stop. Surfacing it as a
data-quality finding keeps a bad input from silently distorting the grade.

Only fires when a profile is active. Stops that took the profile default (because
the CSV left service time blank) are in-band by construction, so this flags only
genuinely out-of-band values the operator actually supplied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from routebench.analysis.tools import ApplicabilityResult
from routebench.core.findings import Finding, FindingEvidence, FindingReference

if TYPE_CHECKING:
    from routebench.core.industry import IndustryProfile
    from routebench.core.schemas import Fleet, Route


class ServiceSanityAnalysis:
    """Flags stops with a service time outside the active industry's plausible band."""

    name: str = "analyze_service_sanity"
    description: str = "Flag stops whose service time is implausible for the chosen industry"
    requires_matrix: bool = False

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult:
        if any(r.stops for r in fleet.routes):
            return ApplicabilityResult(is_applicable=True, reason="Fleet has stops to check")
        return ApplicabilityResult(is_applicable=False, reason="No stops")

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]:
        profile: IndustryProfile | None = kwargs.get("industry_profile")  # type: ignore[assignment]
        if profile is None:
            # No industry chosen -> no band to judge against.
            return []

        findings: list[Finding] = []
        for route in fleet.routes:
            finding = self._check_route(route, profile)
            if finding is not None:
                findings.append(finding)
        return findings

    def _check_route(self, route: Route, profile: IndustryProfile) -> Finding | None:
        low, high = profile.service_minutes_band
        offenders = [
            s for s in route.stops if s.service_time_minutes < low or s.service_time_minutes > high
        ]
        if not offenders:
            return None

        worst = max(offenders, key=lambda s: abs(s.service_time_minutes - (low + high) / 2))
        return Finding(
            category="data_quality",
            severity="medium",
            confidence=0.9,
            title=f"Route {route.route_id}: service times outside the {profile.label} range",
            evidence=[
                FindingEvidence(
                    metric_name="out_of_band_service_stops",
                    actual_value=float(len(offenders)),
                    unit="stops",
                ),
                FindingEvidence(
                    metric_name="worst_service_minutes",
                    actual_value=float(worst.service_time_minutes),
                    comparison_value=high if worst.service_time_minutes > high else low,
                    comparison_type="threshold",
                    unit="minutes",
                ),
            ],
            references=FindingReference(
                route_ids=[route.route_id],
                stop_sequences=[(route.route_id, s.stop_sequence) for s in offenders],
            ),
            hypothesis=(
                f"{len(offenders)} stop(s) on this route have a service time outside the "
                f"plausible {low:g}-{high:g} min range for {profile.label} (worst: "
                f"{worst.service_time_minutes:g} min). That usually means a mis-keyed value, a "
                "units mix-up, or the wrong industry profile — not a real operation. An "
                "out-of-band service time distorts the time and compliance scores, so it is "
                "worth correcting before trusting the grade."
            ),
            suggested_investigation=(
                "Check the service_time_minutes column for these stops, and confirm the chosen "
                "industry profile matches this operation."
            ),
        )
