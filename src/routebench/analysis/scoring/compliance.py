"""Compliance scoring: time window violations, shift overrun, lunch compliance."""

from __future__ import annotations

from routebench.core.config import WorkRules
from routebench.core.schemas import Route


def compute_compliance_metrics(
    route: Route,
    time_metrics: dict[str, object],
    work_rules: WorkRules,
) -> dict[str, object]:
    """Compute compliance metrics for a single route.

    Time-window violations come from the schedule propagated through the matrix
    in `analysis.scoring.time`, not from the optional `planned_arrival_time` CSV
    column. The column is a static value produced by whoever built the plan: it
    does not respond to the travel times we measure, is absent on most uploads
    (silently yielding zero violations), and would grade the plan on a different
    clock than the benchmark. Sharing one clock is what lets a traffic profile
    tighten time-window feasibility.

    Returns dict with:
    - time_window_violations: count of stops reached after window_end
    - shift_overrun_minutes: from time_metrics
    - lunch_taken_within_window: bool
    - depot_return_after_cutoff: None (not yet implemented)
    """
    raw_violations = time_metrics.get("time_window_violations", 0)
    violations = int(raw_violations) if isinstance(raw_violations, (int, float)) else 0

    raw_overrun = time_metrics.get("shift_overrun_minutes", 0.0)
    shift_overrun = float(raw_overrun) if isinstance(raw_overrun, (int, float)) else 0.0

    raw_total = time_metrics.get("total_time_hours", 0.0)
    total_time = float(raw_total) if isinstance(raw_total, (int, float)) else 0.0
    raw_idle = time_metrics.get("idle_time_hours", 0.0)
    idle_time = float(raw_idle) if isinstance(raw_idle, (int, float)) else 0.0
    lunch_hours = work_rules.lunch_minutes / 60.0

    # Lunch should have been taken if shift > lunch_after_hours
    shift_exceeds_lunch_threshold = total_time > work_rules.lunch_after_hours
    lunch_taken = idle_time >= lunch_hours if shift_exceeds_lunch_threshold else True

    return {
        "time_window_violations": violations,
        "shift_overrun_minutes": shift_overrun,
        "lunch_taken_within_window": lunch_taken,
        "depot_return_after_cutoff": None,
    }
