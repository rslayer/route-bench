"""The solvers honour time windows.

They are named TSPTW and VRPTW — TSP/VRP *with time windows* — but until now
they bounded only total shift length and never constrained a window. The
benchmark could therefore beat a plan by breaking promises that plan kept, and
the sequencing grade was measured against a tour that could not legally be
driven. These tests pin the fix.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import numpy as np
import pytest

from routebench.analysis.benchmark.tsptw import solve_tsptw
from routebench.analysis.benchmark.windows import (
    route_start_seconds,
    seconds_since_midnight,
    stop_window,
)
from routebench.core.config import WorkRules
from routebench.core.schemas import Route, Stop
from routebench.infra.matrix.base import MatrixResult

# Stops on an east-west line through the depot, so travel cost is |Δx|.
# Minutes per unit is chosen so a tour spans a plausible morning.
POSITIONS = {0: 0.0, 1: 1.0, 2: 2.0, 3: -5.0}
SECONDS_PER_UNIT = 300.0
METERS_PER_UNIT = 1609.0


def _line_matrix(positions: dict[int, float]) -> MatrixResult:
    n = len(positions)
    distances = np.zeros((n, n))
    durations = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            delta = abs(positions[i] - positions[j])
            distances[i][j] = delta * METERS_PER_UNIT
            durations[i][j] = delta * SECONDS_PER_UNIT
    return MatrixResult(
        durations_seconds=durations.tolist(),
        distances_meters=distances.tolist(),
        provider="line",
        cached=False,
    )


def _stop(seq: int, lon: float, window: tuple[time, time] | None = None) -> Stop:
    return Stop(
        route_id="R1",
        stop_sequence=seq,
        latitude=0.0,
        longitude=lon,
        service_time_minutes=0.0,
        time_window_start=window[0] if window else None,
        time_window_end=window[1] if window else None,
    )


def _route(stops: list[Stop], start_hour: int = 8) -> Route:
    return Route(
        route_id="R1",
        stops=stops,
        depot_lat=0.0,
        depot_lon=0.0,
        planned_start_time=datetime(2025, 1, 15, start_hour, 0, tzinfo=UTC),
    )


def _is_feasible(order: list[int], stops: list[Stop], start_hour: int = 8) -> bool:
    """Walk a tour and check every window is met — the ground truth."""
    now = start_hour * 3600.0
    prev = 0
    for node in order:
        now += abs(POSITIONS[prev] - POSITIONS[node]) * SECONDS_PER_UNIT
        stop = stops[node - 1]
        if stop.time_window_end is not None and now > seconds_since_midnight(stop.time_window_end):
            return False
        if stop.time_window_start is not None:
            now = max(now, seconds_since_midnight(stop.time_window_start))
        prev = node
    return True


class TestWindowHelpers:
    def test_seconds_since_midnight(self) -> None:
        assert seconds_since_midnight(time(9, 30)) == 9 * 3600 + 30 * 60

    def test_route_start(self) -> None:
        assert route_start_seconds(_route([], start_hour=7)) == 7 * 3600

    def test_no_window_is_unbounded(self) -> None:
        assert stop_window(_stop(1, 1.0), horizon_end=86_400) is None

    def test_open_ended_start_bounds_only_the_close(self) -> None:
        stop = _stop(1, 1.0)
        stop.time_window_end = time(17, 0)
        assert stop_window(stop, horizon_end=86_400) == (0, 17 * 3600)

    def test_open_ended_end_bounds_only_the_open(self) -> None:
        stop = _stop(1, 1.0)
        stop.time_window_start = time(9, 0)
        assert stop_window(stop, horizon_end=86_400) == (9 * 3600, 86_400)

    def test_backwards_window_widens_rather_than_raising(self) -> None:
        """One contradictory row must not fail the whole analysis."""
        stop = _stop(1, 1.0, (time(17, 0), time(9, 0)))
        assert stop_window(stop, horizon_end=86_400) == (0, 86_400)


class TestTSPTWHonoursWindows:
    """The behaviour that was missing entirely."""

    def _tight_far_stop_route(self) -> Route:
        # The far stop (3) must be served 08:00-08:45; the near ones open at
        # 10:00. Any tour that leaves 3 for later misses its window.
        return _route(
            [
                _stop(1, 1.0, (time(10, 0), time(16, 0))),
                _stop(2, 2.0, (time(10, 0), time(16, 0))),
                _stop(3, -5.0, (time(8, 0), time(8, 45))),
            ]
        )

    def test_enforced_solution_is_feasible(self) -> None:
        route = self._tight_far_stop_route()
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=True), time_limit_s=3
        )
        assert _is_feasible(result.stop_order, route.stops), (
            f"solver returned {result.stop_order}, which breaks a window it was told to honour"
        )

    def test_enforced_solver_serves_the_tight_window_first(self) -> None:
        route = self._tight_far_stop_route()
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=True), time_limit_s=3
        )
        assert result.stop_order[0] == 3

    def test_disabled_solver_ignores_windows(self) -> None:
        """The old behaviour, now reachable only by asking for it."""
        route = self._tight_far_stop_route()
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=False), time_limit_s=3
        )
        assert not _is_feasible(result.stop_order, route.stops)

    def test_the_toggle_actually_changes_the_answer(self) -> None:
        """If these agree the flag is inert, which is the bug being fixed."""
        route = self._tight_far_stop_route()
        matrix = _line_matrix(POSITIONS)
        on = solve_tsptw(route, matrix, WorkRules(enforce_time_windows=True), time_limit_s=3)
        off = solve_tsptw(route, matrix, WorkRules(enforce_time_windows=False), time_limit_s=3)
        assert on.stop_order != off.stop_order


class TestWindowEdgeCases:
    def test_stops_without_windows_are_unconstrained(self) -> None:
        route = _route([_stop(1, 1.0), _stop(2, 2.0), _stop(3, -5.0)])
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=True), time_limit_s=3
        )
        assert sorted(result.stop_order) == [1, 2, 3]

    def test_mixed_windowed_and_open_stops(self) -> None:
        """Unconstrained stops stay free; the windowed one is still honoured.

        The windowed stop need not come first — several orders reach it in time —
        so the assertion is feasibility, not a position. Pinning an order here
        would test the solver's tie-breaking rather than the constraint.
        """
        route = _route(
            [
                _stop(1, 1.0),
                _stop(2, 2.0),
                _stop(3, -5.0, (time(8, 0), time(8, 45))),
            ]
        )
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=True), time_limit_s=3
        )
        assert sorted(result.stop_order) == [1, 2, 3]
        assert _is_feasible(result.stop_order, route.stops)

    def test_infeasible_windows_still_return_a_tour(self) -> None:
        """An impossible plan must degrade, not crash: the report still needs a
        benchmark row, and the plan's own violations are graded separately."""
        route = _route(
            [
                _stop(1, 1.0, (time(8, 0), time(8, 1))),
                _stop(2, 2.0, (time(8, 0), time(8, 1))),
                _stop(3, -5.0, (time(8, 0), time(8, 1))),
            ]
        )
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=True), time_limit_s=3
        )
        assert sorted(result.stop_order) == [1, 2, 3]

    def test_single_stop_route_is_unaffected(self) -> None:
        route = _route([_stop(1, 1.0, (time(9, 0), time(10, 0)))])
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=True), time_limit_s=3
        )
        assert result.stop_order == [1]

    def test_windows_are_wall_clock_not_elapsed(self) -> None:
        """A 09:00 window means 09:00, not nine hours after departure.

        Without pinning the start cumul to the planned departure, the dimension
        starts at zero and the solver satisfies "09:00" by driving for nine
        hours — the tour looks feasible and is nonsense.
        """
        route = _route(
            [
                _stop(1, 1.0, (time(8, 5), time(8, 30))),
                _stop(2, 2.0, (time(10, 0), time(16, 0))),
            ],
            start_hour=8,
        )
        result = solve_tsptw(
            route, _line_matrix(POSITIONS), WorkRules(enforce_time_windows=True), time_limit_s=3
        )
        # Stop 1 opens 5 minutes after departure and closes at 08:30; stop 2 is
        # not reachable before 10:00. Only 1-then-2 works on a real clock.
        assert result.stop_order == [1, 2]
        assert _is_feasible(result.stop_order, route.stops)


@pytest.mark.parametrize("enforce", [True, False])
def test_solver_returns_every_stop_either_way(enforce: bool) -> None:
    route = _route(
        [
            _stop(1, 1.0, (time(10, 0), time(16, 0))),
            _stop(2, 2.0, (time(10, 0), time(16, 0))),
            _stop(3, -5.0, (time(8, 0), time(8, 45))),
        ]
    )
    result = solve_tsptw(
        route,
        _line_matrix(POSITIONS),
        WorkRules(enforce_time_windows=enforce),
        time_limit_s=3,
    )
    assert sorted(result.stop_order) == [1, 2, 3], "a benchmark must visit every stop"
