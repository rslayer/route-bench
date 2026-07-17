"""Time-window constraints for the OR-Tools solvers.

Shared by TSPTW and VRPTW so the two cannot drift: a window means the same thing
whichever solver reads it.

Why this exists: the solvers previously bounded only total shift length. They
were named for time windows but never constrained them, so the benchmark was
free to beat a plan by breaking promises that plan kept — and the sequencing
grade was measured against a tour that could be infeasible in reality. A
benchmark that cheats is worse than no benchmark.
"""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from routebench.core.schemas import Route, Stop

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# OR-Tools works in integer seconds on a dimension that starts at zero. The
# whole schedule is expressed relative to midnight so a wall-clock window
# ("09:00") lands on the same axis as a cumulative travel time.
SECONDS_PER_DAY = 86_400


def seconds_since_midnight(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def route_start_seconds(route: Route) -> int:
    """When the vehicle leaves the depot, seconds since midnight."""
    start = route.planned_start_time
    return start.hour * 3600 + start.minute * 60 + start.second


def stop_window(stop: Stop, horizon_end: int) -> tuple[int, int] | None:
    """A stop's (open, close) in seconds since midnight, or None if unbounded.

    A one-sided window is still a window: "no earlier than 09:00" bounds the
    open edge and leaves the close at the horizon, and vice versa. Returning
    None only for stops with neither edge keeps unconstrained stops genuinely
    unconstrained rather than silently pinned to the whole day.
    """
    if stop.time_window_start is None and stop.time_window_end is None:
        return None
    open_s = seconds_since_midnight(stop.time_window_start) if stop.time_window_start else 0
    close_s = seconds_since_midnight(stop.time_window_end) if stop.time_window_end else horizon_end
    # A window that closes before it opens is contradictory input. Widening to
    # the horizon rather than raising keeps one bad row from failing the whole
    # analysis; the plan is still graded against the window as written, so the
    # contradiction surfaces as a compliance finding rather than a crash.
    if close_s < open_s:
        logger.warning(
            "time_window_closes_before_it_opens",
            route_id=stop.route_id,
            stop_sequence=stop.stop_sequence,
            start=str(stop.time_window_start),
            end=str(stop.time_window_end),
        )
        return (0, horizon_end)
    return (open_s, close_s)


def apply_time_windows(
    routing: Any,
    manager: Any,
    dimension_name: str,
    stops_by_node: dict[int, Stop],
    start_seconds_by_vehicle: dict[int, int],
    horizon_end: int,
) -> int:
    """Constrain each node's arrival to its stop's window. Returns how many applied.

    `stops_by_node` maps a routing node index to its stop; node 0 (the depot) is
    absent and stays unconstrained.

    The vehicle's start cumul is pinned to the route's planned departure so the
    dimension reads as wall-clock time. Without that pin the dimension starts at
    zero and a "09:00" window would be compared against elapsed seconds — the
    solver would satisfy it by driving for nine hours first.
    """
    dimension = routing.GetDimensionOrDie(dimension_name)
    applied = 0

    for node, stop in stops_by_node.items():
        window = stop_window(stop, horizon_end)
        if window is None:
            continue
        index = manager.NodeToIndex(node)
        if index < 0:
            continue
        dimension.CumulVar(index).SetRange(*window)
        applied += 1

    for vehicle, start_seconds in start_seconds_by_vehicle.items():
        start_index = routing.Start(vehicle)
        dimension.CumulVar(start_index).SetRange(start_seconds, start_seconds)
        # OR-Tools drops start/end cumuls from the objective by default, which
        # would leave them free to take any consistent value. Both must stay
        # pinned for wall-clock windows to mean anything.
        routing.AddVariableMinimizedByFinalizer(dimension.CumulVar(start_index))
        routing.AddVariableMinimizedByFinalizer(dimension.CumulVar(routing.End(vehicle)))

    return applied
