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

    Returns dict with:
    - time_window_violations: count of stops where planned arrival > window_end
    - shift_overrun_minutes: from time_metrics
    - lunch_taken_within_window: bool
    - depot_return_after_cutoff: None (not yet implemented)
    """
    # Count time window violations
    violations = 0
    for stop in route.stops:
        if stop.time_window_end is not None and stop.planned_arrival_time is not None:
            arrival_time = stop.planned_arrival_time.time()
            if arrival_time > stop.time_window_end:
                violations += 1

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
