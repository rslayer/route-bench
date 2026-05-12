"""Time scoring: compute drive time, service time, idle time, shift overrun."""

from __future__ import annotations

from datetime import datetime, time

from routebench.core.config import WorkRules
from routebench.core.schemas import Route
from routebench.infra.matrix.base import MatrixResult


def _time_to_seconds_since_midnight(t: time) -> float:
    return t.hour * 3600.0 + t.minute * 60.0 + t.second


def _datetime_to_seconds_since_midnight(dt: datetime) -> float:
    return dt.hour * 3600.0 + dt.minute * 60.0 + dt.second


def compute_time_metrics(
    route: Route,
    matrix: MatrixResult,
    work_rules: WorkRules,
) -> dict[str, object]:
    """Compute time metrics for a single route.

    Walks stops in sequence, tracking absolute time from planned_start_time.
    At each stop:
    - arrival = previous_departure + travel_time
    - If stop has time_window_start and arrival < window_start, idle until window
    - Insert lunch after lunch_after_hours of elapsed shift time
    - departure = max(arrival, window_start) + service_time

    Returns dict with:
    - drive_time_hours, service_time_hours, idle_time_hours, total_time_hours
    - shift_overrun_minutes
    - leg_durations_seconds: per-leg travel times
    - time_window_violations: count of stops where arrival > time_window_end
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
            "time_window_violations": 0,
        }

    durations = matrix.durations_array()

    drive_time_seconds = 0.0
    service_time_seconds = 0.0
    idle_time_seconds = 0.0
    tw_idle_seconds = 0.0
    leg_durations: list[float] = []
    time_window_violations = 0

    # Track absolute time (seconds since midnight) for time-window checks
    start_seconds = _datetime_to_seconds_since_midnight(route.planned_start_time)

    # Pre-trip time
    pre_trip_seconds = work_rules.pre_trip_minutes * 60.0
    current_time = start_seconds + pre_trip_seconds

    # Track elapsed shift time for lunch insertion
    elapsed_shift_seconds = pre_trip_seconds
    lunch_taken = False
    lunch_threshold_seconds = work_rules.lunch_after_hours * 3600.0

    def _process_stop(stop_idx: int, travel_seconds: float) -> None:
        nonlocal current_time, elapsed_shift_seconds, drive_time_seconds
        nonlocal service_time_seconds, idle_time_seconds, tw_idle_seconds
        nonlocal lunch_taken, time_window_violations

        stop = route.stops[stop_idx]

        # Travel
        drive_time_seconds += travel_seconds
        current_time += travel_seconds
        elapsed_shift_seconds += travel_seconds

        # Time window idle: if we arrive before window_start, wait
        if stop.time_window_start is not None:
            window_start_sec = _time_to_seconds_since_midnight(stop.time_window_start)
            if current_time < window_start_sec:
                wait = window_start_sec - current_time
                tw_idle_seconds += wait
                idle_time_seconds += wait
                current_time = window_start_sec
                elapsed_shift_seconds += wait

        # Time window violation: if we arrive after window_end
        if stop.time_window_end is not None:
            window_end_sec = _time_to_seconds_since_midnight(stop.time_window_end)
            if current_time > window_end_sec:
                time_window_violations += 1

        # Insert lunch if threshold reached and not yet taken
        if not lunch_taken and elapsed_shift_seconds >= lunch_threshold_seconds:
            lunch_seconds = work_rules.lunch_minutes * 60.0
            idle_time_seconds += lunch_seconds
            current_time += lunch_seconds
            elapsed_shift_seconds += lunch_seconds
            lunch_taken = True

        # Service time
        svc = stop.service_time_minutes * 60.0
        service_time_seconds += svc
        current_time += svc
        elapsed_shift_seconds += svc

    # Depot to first stop
    travel_depot_to_first = float(durations[0, 1])
    leg_durations.append(travel_depot_to_first)
    _process_stop(0, travel_depot_to_first)

    # Process remaining stops
    for i in range(1, n_stops):
        prev_idx = i  # matrix index for previous stop (1-based)
        curr_idx = i + 1  # matrix index for current stop

        travel = float(durations[prev_idx, curr_idx])
        leg_durations.append(travel)
        _process_stop(i, travel)

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
        "time_window_violations": time_window_violations,
    }
