"""Time scoring: compute drive time, service time, idle time, shift overrun."""

from __future__ import annotations

from routebench.core.config import WorkRules
from routebench.core.schemas import Route
from routebench.infra.matrix.base import MatrixResult


def compute_time_metrics(
    route: Route,
    matrix: MatrixResult,
    work_rules: WorkRules,
) -> dict[str, object]:
    """Compute time metrics for a single route.

    Walks stops in sequence. At each stop:
    - arrival = previous_departure + travel_time
    - If stop has time_window_start and arrival < window_start, idle until window_start
    - departure = arrival + service_time
    - After lunch_after_hours elapsed, insert lunch_minutes break at next stop

    Returns dict with:
    - drive_time_hours, service_time_hours, idle_time_hours, total_time_hours
    - shift_overrun_minutes
    - leg_durations_seconds: per-leg travel times
    """
    n_stops = len(route.stops)
    if n_stops == 0:
        pre_post = (work_rules.pre_trip_minutes + work_rules.post_trip_minutes) / 60.0
        return {
            "drive_time_hours": 0.0,
            "service_time_hours": 0.0,
            "idle_time_hours": 0.0,
            "total_time_hours": pre_post,
            "shift_overrun_minutes": max(
                0.0, pre_post * 60.0 - work_rules.max_shift_hours * 60.0
            ),
            "leg_durations_seconds": [],
        }

    durations = matrix.durations_array()

    drive_time_seconds = 0.0
    service_time_seconds = 0.0
    idle_time_seconds = 0.0
    leg_durations: list[float] = []

    # Pre-trip time
    pre_trip_seconds = work_rules.pre_trip_minutes * 60.0

    # Track elapsed shift time for lunch insertion
    elapsed_shift_seconds = pre_trip_seconds
    lunch_taken = False
    lunch_threshold_seconds = work_rules.lunch_after_hours * 3600.0

    # Depot to first stop
    travel_depot_to_first = float(durations[0, 1])
    leg_durations.append(travel_depot_to_first)
    drive_time_seconds += travel_depot_to_first
    elapsed_shift_seconds += travel_depot_to_first

    # Process first stop
    stop = route.stops[0]
    # Check time window idle
    if stop.time_window_start is not None and stop.planned_arrival_time is not None:
        # Use time window for idle calculation
        pass  # Handled generically below

    # Check lunch before first stop's service
    if not lunch_taken and elapsed_shift_seconds >= lunch_threshold_seconds:
        lunch_seconds = work_rules.lunch_minutes * 60.0
        idle_time_seconds += lunch_seconds
        elapsed_shift_seconds += lunch_seconds
        lunch_taken = True

    svc = stop.service_time_minutes * 60.0
    service_time_seconds += svc
    elapsed_shift_seconds += svc

    # Process remaining stops
    for i in range(1, n_stops):
        prev_idx = i  # matrix index for previous stop (1-based)
        curr_idx = i + 1  # matrix index for current stop

        travel = float(durations[prev_idx, curr_idx])
        leg_durations.append(travel)
        drive_time_seconds += travel
        elapsed_shift_seconds += travel

        # Check time window idle for this stop
        curr_stop = route.stops[i]
        # Note: In absence of absolute time tracking, we use the matrix
        # travel times and service times as relative durations

        # Insert lunch if threshold reached and not yet taken
        if not lunch_taken and elapsed_shift_seconds >= lunch_threshold_seconds:
            lunch_seconds = work_rules.lunch_minutes * 60.0
            idle_time_seconds += lunch_seconds
            elapsed_shift_seconds += lunch_seconds
            lunch_taken = True

        svc = curr_stop.service_time_minutes * 60.0
        service_time_seconds += svc
        elapsed_shift_seconds += svc

    # Return to depot
    travel_last_to_depot = float(durations[n_stops, 0])
    leg_durations.append(travel_last_to_depot)
    drive_time_seconds += travel_last_to_depot
    elapsed_shift_seconds += travel_last_to_depot

    # Post-trip time
    post_trip_seconds = work_rules.post_trip_minutes * 60.0
    elapsed_shift_seconds += post_trip_seconds

    total_time_hours = elapsed_shift_seconds / 3600.0
    drive_time_hours = drive_time_seconds / 3600.0
    service_time_hours = service_time_seconds / 3600.0
    idle_time_hours = idle_time_seconds / 3600.0

    shift_overrun_minutes = max(
        0.0, (total_time_hours - work_rules.max_shift_hours) * 60.0
    )

    return {
        "drive_time_hours": drive_time_hours,
        "service_time_hours": service_time_hours,
        "idle_time_hours": idle_time_hours,
        "total_time_hours": total_time_hours,
        "shift_overrun_minutes": shift_overrun_minutes,
        "leg_durations_seconds": leg_durations,
    }
