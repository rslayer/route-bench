"""Grading engine — the quality score and its five dimensions.

Pure functions of the scorecard and benchmark results. No LLM touches a grade:
report prose about grades comes from templated slots whose source data is the
grade object itself, so the Phase 10.5 verifier checks it like any other claim.

Three principles decide where each dimension anchors:

* Benchmark-anchored where a solver reference exists. Comparing a plan to what
  the solvers proved achievable on this fleet's own stops under this fleet's own
  constraints is the fairest reference available.
* Fleet-relative where industry context would otherwise be needed. Stop density
  varies by industry; consistency across a fleet's own routes does not.
* Absolute only where absolutes are honest. A missed time window is a missed
  time window in any industry.

CALIBRATION HONESTY: the v1.0 breakpoints are engineering judgment, not
empirical calibration — no real upload distribution existed when they were set.
Telemetry logs the score distribution (dimension scores only, never fleet data)
so they can be recalibrated once uploads accumulate. Any change to a breakpoint
or weight bumps GRADING_VERSION, and reports permanently display the version
they were graded under, so an old report is never silently reinterpreted.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from itertools import pairwise
from typing import TYPE_CHECKING

import structlog

from routebench.core.findings import (
    BenchmarkResult,
    DimensionGrade,
    FleetMetrics,
    Grade,
    OverallGrade,
    RouteMetrics,
)

if TYPE_CHECKING:
    from routebench.core.findings import Finding

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Minor bump for breakpoint changes, major for dimension or weight changes.
GRADING_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Breakpoint tables. (input, score) ascending by input; linear between points,
# clamped outside. Every one of these is a judgment call — see the module note.
# ---------------------------------------------------------------------------

Breakpoints = tuple[tuple[float, float], ...]

# Improvement gap % vs the solver -> score. Shared by sequencing and the fleet
# assignment sub-score, since both measure the same thing at different scopes.
GAP_PCT_BREAKPOINTS: Breakpoints = (
    (0, 100),
    (3, 92),
    (7, 82),
    (12, 70),
    (20, 55),
    (35, 35),
    (50, 15),
)

# Fallback when no benchmark ran: actual/nearest-neighbour tour ratio.
SEQUENCING_INDEX_BREAKPOINTS: Breakpoints = (
    (1.00, 95),
    (1.05, 88),
    (1.15, 75),
    (1.30, 60),
    (1.50, 40),
    (2.00, 15),
)

# Coefficient of variation of per-route total time -> score.
TIME_CV_BREAKPOINTS: Breakpoints = (
    (0.05, 100),
    (0.15, 85),
    (0.25, 68),
    (0.40, 45),
    (0.60, 20),
)

# Fleet idle hours / total hours -> score.
IDLE_RATIO_BREAKPOINTS: Breakpoints = (
    (0.02, 100),
    (0.05, 90),
    (0.10, 75),
    (0.18, 55),
    (0.30, 30),
    (0.45, 10),
)

# Share of routes running past the shift cap -> score.
OVERRUN_SHARE_BREAKPOINTS: Breakpoints = (
    (0, 100),
    (0.1, 80),
    (0.25, 60),
    (0.5, 35),
    (0.75, 15),
)

# Time-window violations as a % of stops that have a window -> score.
VIOLATION_RATE_BREAKPOINTS: Breakpoints = (
    (0, 100),
    (1, 90),
    (3, 78),
    (6, 62),
    (12, 40),
    (25, 15),
)

# CV of stops-per-mile across routes -> score.
DISPERSION_CV_BREAKPOINTS: Breakpoints = (
    (0.10, 100),
    (0.25, 85),
    (0.45, 65),
    (0.70, 45),
    (1.00, 25),
)

WEIGHTS: dict[str, float] = {
    "sequencing": 0.25,
    "fleet": 0.20,
    "time": 0.20,
    "compliance": 0.20,
    "density": 0.15,
}

LABELS: dict[str, str] = {
    "sequencing": "Sequencing Efficiency",
    "fleet": "Fleet Assignment & Balance",
    "time": "Time Discipline",
    "compliance": "Compliance",
    "density": "Density & Territory",
}

# Score floor for each letter, highest first.
# ASCII hyphen, not U+2212: this string is a data value that crosses
# Python -> JSON -> TypeScript -> HTML, and a consumer comparing against
# "A-" would fail silently against a typographic minus. Presentation layers
# are free to render it prettily.
LETTER_BANDS: tuple[tuple[float, str], ...] = (
    (97, "A+"),
    (93, "A"),
    (90, "A-"),
    (87, "B+"),
    (83, "B"),
    (80, "B-"),
    (77, "C+"),
    (73, "C"),
    (70, "C-"),
    (67, "D+"),
    (63, "D"),
    (60, "D-"),
)

_TERRITORY_OVERLAP_PENALTY = 12.0
_TERRITORY_FLOOR = 20.0
_OVERRUN_MINUTES_PENALTY_PER_10 = 1.0


def interpolate(value: float, breakpoints: Breakpoints) -> float:
    """Piecewise-linear lookup, clamped outside the table.

    Clamping is the point: a 90% gap is not meaningfully worse than a 50% gap —
    both are "the plan is far off" — and extrapolating would run the score
    negative and make the composite meaningless.
    """
    if not breakpoints:
        return 0.0
    if value <= breakpoints[0][0]:
        return breakpoints[0][1]
    if value >= breakpoints[-1][0]:
        return breakpoints[-1][1]

    for (x0, y0), (x1, y1) in pairwise(breakpoints):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            t = (value - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return breakpoints[-1][1]


def letter_for(score: float) -> str:
    """Letter band for a 0-100 score."""
    for floor, letter in LETTER_BANDS:
        if score >= floor:
            return letter
    return "F"


def _cv(values: list[float]) -> float | None:
    """Coefficient of variation, or None when it is undefined or meaningless.

    Needs at least two values (one route has nothing to vary against) and a
    non-zero mean (dividing by it would explode).

    Non-finite values are dropped, not passed through: an unreachable leg makes a
    route's time `inf`, and `statistics.stdev` raises on inf/nan rather than
    returning it — so an infeasible route would crash the whole grade instead of
    simply not participating in the fleet's variation.
    """
    usable = [v for v in values if v is not None and math.isfinite(v)]
    if len(usable) < 2:
        return None
    mean = statistics.fmean(usable)
    if mean <= 0:
        return None
    return statistics.stdev(usable) / mean


def _stop_weighted_gap(
    route_metrics: dict[str, RouteMetrics],
    benchmark: BenchmarkResult,
) -> float | None:
    """Stop-weighted mean of per-route improvement gaps.

    Weighted by stops, not routes: a 40-stop route being badly sequenced matters
    more than a 3-stop route, and an unweighted mean would let a tiny route drag
    the fleet's grade around.
    """
    total_stops = 0
    weighted = 0.0
    for route_id, rb in benchmark.per_route.items():
        metrics = route_metrics.get(route_id)
        if metrics is None or metrics.stop_count <= 0:
            continue
        weighted += rb.improvement_gap_pct * metrics.stop_count
        total_stops += metrics.stop_count
    if total_stops == 0:
        return None
    return weighted / total_stops


def _not_graded(key: str, basis: str, reason: str) -> DimensionGrade:
    return DimensionGrade(
        key=key,
        label=LABELS[key],
        score=None,
        letter=None,
        basis=basis,
        not_graded=True,
        inputs={"reason": reason},
        explanation_slot_id=f"grade_{key}",
    )


def _graded(key: str, score: float, basis: str, inputs: Mapping[str, float]) -> DimensionGrade:
    clamped = max(0.0, min(100.0, score))
    return DimensionGrade(
        key=key,
        label=LABELS[key],
        score=clamped,
        letter=letter_for(clamped),
        basis=basis,
        not_graded=False,
        inputs=dict(inputs),
        explanation_slot_id=f"grade_{key}",
    )


def grade_sequencing(
    route_metrics: dict[str, RouteMetrics],
    benchmark: BenchmarkResult | None,
) -> DimensionGrade:
    """How well each route's stops are ordered, against the solver's tour."""
    if benchmark is not None and benchmark.per_route:
        gap = _stop_weighted_gap(route_metrics, benchmark)
        if gap is not None:
            # A negative gap means the solver found nothing better; that is a
            # perfect sequencing result, and interpolate clamps it to 100.
            return _graded(
                "sequencing",
                interpolate(gap, GAP_PCT_BREAKPOINTS),
                "benchmark",
                {"stop_weighted_gap_pct": round(gap, 4)},
            )

    indices = [m.sequencing_index for m in route_metrics.values() if m.sequencing_index is not None]
    if not indices:
        return _not_graded(
            "sequencing", "insufficient_data", "no benchmark and no sequencing index"
        )

    mean_index = statistics.fmean(indices)
    return _graded(
        "sequencing",
        interpolate(mean_index, SEQUENCING_INDEX_BREAKPOINTS),
        "heuristic",
        {"mean_sequencing_index": round(mean_index, 4)},
    )


def grade_fleet(
    route_metrics: dict[str, RouteMetrics],
    benchmark: BenchmarkResult | None,
) -> DimensionGrade:
    """Whether the right stops are on the right routes, and the load is even."""
    times = [m.total_time_hours for m in route_metrics.values()]
    time_cv = _cv(times)

    if time_cv is None:
        return _not_graded("fleet", "insufficient_routes", "needs at least 2 routes")

    balance = interpolate(time_cv, TIME_CV_BREAKPOINTS)
    inputs: dict[str, float] = {"time_cv": round(time_cv, 4)}

    # Capacity spread, where the data exists, is a second view of the same
    # question: is the work shared evenly?
    cap_values: list[float] = []
    for metrics in route_metrics.values():
        if metrics.capacity_utilization:
            cap_values.append(statistics.fmean(list(metrics.capacity_utilization.values())))
    cap_cv = _cv(cap_values) if len(cap_values) == len(route_metrics) else None
    if cap_cv is not None:
        balance = statistics.fmean([balance, interpolate(cap_cv, TIME_CV_BREAKPOINTS)])
        inputs["capacity_cv"] = round(cap_cv, 4)

    fleet_level = benchmark.fleet_level if benchmark is not None else None
    if fleet_level is None:
        # Skipped for single-route fleets, multi-depot fleets, fleets over the
        # stop cap, or a disabled benchmark. Balance still says something real,
        # so grade on it alone rather than dropping the dimension.
        return _graded("fleet", balance, "balance_only", inputs)

    route_gap = _stop_weighted_gap(route_metrics, benchmark) if benchmark else None
    # Only the gap *beyond* what route-level resequencing already explains is
    # an assignment problem; without this subtraction the same waste would be
    # penalised twice, once here and once in sequencing.
    incremental = max(0.0, fleet_level.improvement_gap_pct - (route_gap or 0.0))
    assignment = interpolate(incremental, GAP_PCT_BREAKPOINTS)
    inputs["fleet_gap_pct"] = round(fleet_level.improvement_gap_pct, 4)
    inputs["incremental_gap_pct"] = round(incremental, 4)

    return _graded("fleet", 0.6 * assignment + 0.4 * balance, "benchmark", inputs)


def grade_time(
    fleet_metrics: FleetMetrics,
    route_metrics: dict[str, RouteMetrics],
) -> DimensionGrade:
    """Idle time and shift overruns — is the day's time being spent well?"""
    if not route_metrics:
        return _not_graded("time", "insufficient_data", "no routes")

    total_time = sum(m.total_time_hours for m in route_metrics.values())
    total_idle = sum(m.idle_time_hours for m in route_metrics.values())
    idle_ratio = (total_idle / total_time) if total_time > 0 else 0.0
    idle_score = interpolate(idle_ratio, IDLE_RATIO_BREAKPOINTS)

    overrunning = [m for m in route_metrics.values() if m.shift_overrun_minutes > 0]
    overrun_share = len(overrunning) / len(route_metrics)
    overrun_score = interpolate(overrun_share, OVERRUN_SHARE_BREAKPOINTS)

    mean_overrun = (
        statistics.fmean([m.shift_overrun_minutes for m in overrunning]) if overrunning else 0.0
    )
    # How far over matters, not just how many: one route 3 hours late is a
    # different problem from one route 3 minutes late.
    overrun_score = max(
        0.0, overrun_score - (mean_overrun / 10.0) * _OVERRUN_MINUTES_PENALTY_PER_10
    )

    return _graded(
        "time",
        0.6 * idle_score + 0.4 * overrun_score,
        "absolute",
        {
            "idle_ratio": round(idle_ratio, 4),
            "overrun_share": round(overrun_share, 4),
            "mean_overrun_minutes": round(mean_overrun, 2),
        },
    )


def grade_compliance(route_metrics: dict[str, RouteMetrics]) -> DimensionGrade:
    """Commitments kept: time windows, and the operational rules around them."""
    if not route_metrics:
        return _not_graded("compliance", "insufficient_data", "no routes")

    # Operational sub-score. depot_return_after_cutoff is not modelled anywhere
    # (no work rule defines a cutoff), so this is lunch alone: including a term
    # that is always zero would hand every fleet an unloseable half of the
    # score and make the dimension look calibrated when it is not. Restoring it
    # is a rubric change and bumps GRADING_VERSION.
    lunch_failures = sum(1 for m in route_metrics.values() if not m.lunch_taken_within_window)
    lunch_fail_share = lunch_failures / len(route_metrics)
    operational = max(0.0, 100.0 * (1.0 - lunch_fail_share))

    stops_with_windows = sum(m.stops_with_windows for m in route_metrics.values())
    if stops_with_windows == 0:
        # No windows means no violation rate exists. Grading it would either
        # invent a perfect score or divide by zero.
        return _graded(
            "compliance",
            operational,
            "operational_only",
            {"lunch_fail_share": round(lunch_fail_share, 4)},
        )

    violations = sum(m.time_window_violations for m in route_metrics.values())
    violation_rate = 100.0 * violations / stops_with_windows
    window_score = interpolate(violation_rate, VIOLATION_RATE_BREAKPOINTS)

    return _graded(
        "compliance",
        0.7 * window_score + 0.3 * operational,
        "absolute",
        {
            "violation_rate_pct": round(violation_rate, 4),
            "violations": float(violations),
            "stops_with_windows": float(stops_with_windows),
            "lunch_fail_share": round(lunch_fail_share, 4),
        },
    )


def _overlapping_pairs(findings: list[Finding]) -> int:
    """Distinct route pairs the territory diagnosis flagged as overlapping.

    Read from `references.route_ids` rather than the finding title: the title is
    prose and would need parsing, which breaks the moment someone rewords it.
    """
    pairs: set[tuple[str, str]] = set()
    for finding in findings:
        if finding.category != "territory":
            continue
        is_overlap = any(e.metric_name == "geographic_overlap_pct" for e in finding.evidence)
        if not is_overlap:
            continue
        route_ids = sorted(finding.references.route_ids)
        if len(route_ids) == 2:
            pairs.add((route_ids[0], route_ids[1]))
    return len(pairs)


def grade_density(
    route_metrics: dict[str, RouteMetrics],
    findings: list[Finding],
) -> DimensionGrade:
    """Dispersion consistency and territory overlap.

    Fleet-relative by design: absolute stop density is an industry fact, not a
    plan quality — a rural fleet is not worse than an urban one. Consistency
    across a fleet's own routes is comparable.
    """
    if len(route_metrics) < 2:
        return _not_graded("density", "insufficient_routes", "needs at least 2 routes")

    dispersion_cv = _cv([m.stops_per_mile for m in route_metrics.values()])
    if dispersion_cv is None:
        return _not_graded("density", "insufficient_data", "no usable stop density")

    dispersion_score = interpolate(dispersion_cv, DISPERSION_CV_BREAKPOINTS)

    n_pairs = _overlapping_pairs(findings)
    overlap_score = max(_TERRITORY_FLOOR, 100.0 - _TERRITORY_OVERLAP_PENALTY * n_pairs)

    return _graded(
        "density",
        0.6 * dispersion_score + 0.4 * overlap_score,
        "fleet_relative",
        {
            "dispersion_cv": round(dispersion_cv, 4),
            "overlapping_route_pairs": float(n_pairs),
        },
    )


def compute_grade(
    fleet_metrics: FleetMetrics,
    route_metrics: dict[str, RouteMetrics],
    findings: list[Finding],
    benchmark: BenchmarkResult | None = None,
    weights: dict[str, float] | None = None,
) -> Grade:
    """Grade a fleet. Deterministic: same inputs, same grade, always.

    `weights` overrides the composite blend (e.g. from an industry profile);
    None uses the industry-agnostic default. Dimension scores are unaffected —
    only how they are combined — so they stay comparable across industries.
    """
    active_weights = weights if weights is not None else WEIGHTS
    dimensions = [
        grade_sequencing(route_metrics, benchmark),
        grade_fleet(route_metrics, benchmark),
        grade_time(fleet_metrics, route_metrics),
        grade_compliance(route_metrics),
        grade_density(route_metrics, findings),
    ]

    # Renormalize over what was actually graded, so an ungraded dimension does
    # not silently score zero and drag the composite down.
    graded = [d for d in dimensions if not d.not_graded and d.score is not None]
    total_weight = sum(active_weights[d.key] for d in graded)

    if total_weight <= 0:
        overall = OverallGrade(score=None, letter=None)
    else:
        score = sum(active_weights[d.key] * (d.score or 0.0) for d in graded) / total_weight
        overall = OverallGrade(score=score, letter=letter_for(score))

    grade = Grade(
        grading_version=GRADING_VERSION,
        overall=overall,
        weights=dict(active_weights),
        dimensions=dimensions,
    )

    logger.info(
        "grade_computed",
        grading_version=GRADING_VERSION,
        overall=round(overall.score, 2) if overall.score is not None else None,
        letter=overall.letter,
        not_graded=[d.key for d in dimensions if d.not_graded],
    )
    return grade
