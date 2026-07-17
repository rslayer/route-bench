"""Regression guards: contradictory time windows and malformed time strings.

Both found by robustness run 3, both the same "silently accepted, analysis
then wrong" class as earlier fixes. Promoted from tests/adversary/ as the fix
landed, and written to assert the behaviour we want plus the guard cases that
must keep passing.

See ROBUSTNESS.md.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, time
from pathlib import Path

from routebench.analysis.scoring.time import compute_time_metrics
from routebench.core.config import WorkRules
from routebench.core.schemas import Route, Stop
from routebench.core.validation import validate_csv
from routebench.infra.matrix.base import MatrixResult


def _one_stop_route(window_start: time | None, window_end: time | None) -> Route:
    return Route(
        route_id="R-001",
        stops=[
            Stop(
                route_id="R-001",
                stop_sequence=1,
                latitude=32.83,
                longitude=-96.77,
                time_window_start=window_start,
                time_window_end=window_end,
            ),
        ],
        depot_lat=32.825,
        depot_lon=-96.775,
        planned_start_time=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
    )


# 5-minute hop depot <-> stop.
_MATRIX = MatrixResult(
    durations_seconds=[[0.0, 300.0], [300.0, 0.0]],
    distances_meters=[[0.0, 1000.0], [1000.0, 0.0]],
    provider="stub",
    cached=False,
)


class TestContradictoryTimeWindow:
    """A window that closes before it opens can never be satisfied. The scoring
    path read window_start as a plain "wait until", so it idled ~9h toward an
    impossible open time — while the benchmark path already widened it away."""

    def test_contradictory_window_does_not_inflate_idle_time(self) -> None:
        route = _one_stop_route(time(17, 0), time(8, 0))
        metrics = compute_time_metrics(route, _MATRIX, WorkRules())
        assert metrics["idle_time_hours"] < 0.5, (
            f"contradictory window (17:00-08:00) inflated idle time to "
            f"{metrics['idle_time_hours']:.2f}h instead of being ignored"
        )

    def test_contradictory_window_flags_no_violation(self) -> None:
        """Widened to no constraint => no idle AND no violation, matching
        benchmark.windows.stop_window. A violation for a meaningless window
        would be as wrong as the inflated idle it replaced."""
        route = _one_stop_route(time(17, 0), time(8, 0))
        metrics = compute_time_metrics(route, _MATRIX, WorkRules())
        assert metrics["time_window_violations"] == 0

    def test_coherent_window_still_idles_and_is_unaffected(self) -> None:
        """The guard: a real window must still make the vehicle wait. Arriving
        before an 09:00 open, it idles ~0.67h — well clear of the near-zero a
        contradictory window collapses to, which is the distinction that
        matters. (This is a control: it passes with or without the fix, since
        the fix only changes the incoherent case.)"""
        route = _one_stop_route(time(9, 0), time(17, 0))
        metrics = compute_time_metrics(route, _MATRIX, WorkRules())
        assert 0.5 < metrics["idle_time_hours"] < 1.5, (
            f"a coherent 09:00-17:00 window should still idle substantially, got "
            f"{metrics['idle_time_hours']:.2f}h"
        )


class TestMalformedTimeSurfaced:
    """An unparseable time string was swallowed to None, indistinguishable from
    an absent column — no error, no warning, no defaults_applied entry."""

    def _validate(self, body: bytes) -> tuple[object, object]:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(body)
            path = Path(f.name)
        try:
            return validate_csv(path)
        finally:
            path.unlink(missing_ok=True)

    def test_garbage_time_is_warned_not_dropped(self) -> None:
        fleet, report = self._validate(
            b"route_id,stop_sequence,latitude,longitude,planned_arrival_time\n"
            b"R-001,0,32.825,-96.775,\n"
            b"R-001,1,32.830,-96.770,not-a-real-time-at-all!!\n"
        )
        assert fleet is not None
        assert fleet.routes[0].stops[0].planned_arrival_time is None
        assert any(w.code == "UNPARSEABLE_TIME" for w in report.warnings), (
            f"a garbage planned_arrival_time was dropped without a warning: "
            f"warnings={report.warnings!r}"
        )

    def test_blank_time_is_silent(self) -> None:
        """The guard: an empty cell legitimately means 'unset' and must NOT
        warn — only content that failed to parse should."""
        fleet, report = self._validate(
            b"route_id,stop_sequence,latitude,longitude,planned_arrival_time\n"
            b"R-001,0,32.825,-96.775,\n"
            b"R-001,1,32.830,-96.770,\n"
        )
        assert fleet is not None
        assert not any(w.code == "UNPARSEABLE_TIME" for w in report.warnings)

    def test_valid_time_parses_without_warning(self) -> None:
        fleet, report = self._validate(
            b"route_id,stop_sequence,latitude,longitude,planned_arrival_time\n"
            b"R-001,0,32.825,-96.775,\n"
            b"R-001,1,32.830,-96.770,2026-01-01T09:30:00\n"
        )
        assert fleet is not None
        assert fleet.routes[0].stops[0].planned_arrival_time is not None
        assert not any(w.code == "UNPARSEABLE_TIME" for w in report.warnings)
