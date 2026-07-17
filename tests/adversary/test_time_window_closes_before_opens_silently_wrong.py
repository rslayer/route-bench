"""Defect: a stop whose time window closes before it opens is accepted as
valid by `validate_csv` (no error, no warning) and then silently produces a
wildly wrong idle-time figure in `compute_time_metrics` — the actual scoring
path used for the report, not just the benchmark solver.

Impact: `time_window_start=17:00, time_window_end=08:00` is contradictory —
no arrival time can ever satisfy it. `validate_csv`
(routebench/core/validation.py) has no check for `start < end` on time
windows (unlike coordinates, which do get an explicit range check), so this
row sails through as `is_valid=True` with zero warnings. Downstream,
`compute_time_metrics` (routebench/analysis/scoring/time.py) treats
`time_window_start` as a pure "wait until this time" instruction with no
sanity check against `time_window_end`: arriving at 8:05am it is "before"
17:00, so the vehicle idles for roughly 9 hours waiting for a window that is
already impossible to hit, then immediately gets flagged as a violation for
missing the 08:00 close it just idled past. The reported idle time and total
shift time are inflated by that same ~9 hours with no indication anywhere
that the input was contradictory.

The sibling module `routebench/analysis/benchmark/windows.py::stop_window`
handles exactly this contradiction correctly: it detects `close_s < open_s`,
logs a warning, and widens the window to the full horizon rather than
producing a nonsensical wait. `compute_time_metrics` has no equivalent
guard, so the scoring path (which is what the report actually shows the
user) silently ships the bad number while the benchmark path next to it does
not.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from routebench.analysis.scoring.time import compute_time_metrics
from routebench.core.config import WorkRules
from routebench.core.schemas import Route, Stop
from routebench.infra.matrix.base import MatrixResult


def test_contradictory_window_does_not_inflate_idle_time() -> None:
    route = Route(
        route_id="R-001",
        stops=[
            Stop(
                route_id="R-001",
                stop_sequence=1,
                latitude=32.83,
                longitude=-96.77,
                # Closes before it opens: no arrival can ever satisfy this.
                time_window_start=time(17, 0),
                time_window_end=time(8, 0),
            ),
        ],
        depot_lat=32.825,
        depot_lon=-96.775,
        planned_start_time=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
    )

    # A 5-minute hop from depot to the only stop.
    matrix = MatrixResult(
        durations_seconds=[[0.0, 300.0], [300.0, 0.0]],
        distances_meters=[[0.0, 1000.0], [1000.0, 0.0]],
        provider="stub",
        cached=False,
    )

    metrics = compute_time_metrics(route, matrix, WorkRules())

    # The vehicle should not be reported as idling for hours to satisfy a
    # time window that is mathematically impossible to satisfy in the first
    # place. Idle time should stay near-zero (a few minutes of travel-related
    # rounding at most), not balloon to ~9 hours.
    assert metrics["idle_time_hours"] < 0.5, (
        "compute_time_metrics silently produced a huge idle-time figure "
        f"({metrics['idle_time_hours']:.2f}h) for a contradictory time "
        "window (start=17:00, end=08:00) instead of flagging/widening it "
        "the way routebench.analysis.benchmark.windows.stop_window does"
    )
