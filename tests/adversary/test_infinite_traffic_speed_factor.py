"""Defect: `TrafficProfile.default_factor` (and `TrafficBand.speed_factor`)
are declared `Field(gt=0)`, meant to keep the multiplier sane. A config
value like `1e400` overflows float parsing to `inf`, and `inf > 0` is
True — so pydantic accepts it. `AnalysisConfig(traffic={"default_factor":
1e400})` builds successfully with `default_factor == float("inf")`.

Impact: `TrafficAdjustedProvider.apply_row_factors` (infra/matrix/traffic.py)
computes `duration / factor` for every leg. With `factor == inf`, every
travel duration becomes `0.0` — the analysis silently treats every leg as
instantaneous. That collapses drive time, inflates stops-per-hour, and
hides shift overruns and time-window violations that a normal-speed
analysis would have found: a `config` JSON supplied on the public,
unauthenticated `POST /sessions` endpoint can silently zero out the
travel-time dimension of the report it produces.

Root cause: `Field(gt=0)` in `core/config.py`'s `TrafficProfile` does not
exclude `float("inf")` (or, symmetrically, subnormal-but-nonzero floats
that make `1/factor` explode). The bound needs an explicit upper limit
(`le=...` or `math.isfinite` check), not just `gt=0`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from routebench.core.config import AnalysisConfig
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.traffic import TrafficAdjustedProvider


class _FixedDurationProvider:
    """Minimal MatrixProvider stub: every leg takes a fixed, non-zero time."""

    name = "fixed"

    def __init__(self, duration_s: float = 300.0) -> None:
        self._duration_s = duration_s

    def get_matrix(self, origins, destinations, departure_time=None, origin_departure_times=None):
        n_o, n_d = len(origins), len(destinations)
        return MatrixResult(
            durations_seconds=[[self._duration_s] * n_d for _ in range(n_o)],
            distances_meters=[[1000.0] * n_d for _ in range(n_o)],
            provider="fixed",
            cached=False,
        )


def test_overflowing_speed_factor_is_rejected_by_config() -> None:
    """1e400 is not a sane speed multiplier: it overflows to float('inf'),
    which Field(gt=0) does not exclude. Constructing the config should fail
    validation rather than silently produce an infinite speed factor."""
    config = AnalysisConfig(traffic={"default_factor": 1e400})
    assert config.traffic.default_factor != float("inf"), (
        "AnalysisConfig accepted an infinite traffic speed_factor "
        "(1e400 overflows to inf, and Field(gt=0) does not exclude inf)"
    )


def test_infinite_speed_factor_does_not_zero_out_travel_time() -> None:
    """Even where construction is allowed, it must not collapse every leg to
    0 seconds of travel time — that silently erases the travel-time
    dimension of the analysis (drive time, shift overrun, stops/hour)."""
    stop = Stop(route_id="R-001", stop_sequence=1, latitude=32.83, longitude=-96.77)
    route = Route(
        route_id="R-001",
        stops=[stop],
        depot_lat=32.8,
        depot_lon=-96.7,
        planned_start_time=datetime.now(UTC),
    )
    Fleet(routes=[route], upload_id="u1", uploaded_at=datetime.now(UTC))

    config = AnalysisConfig(traffic={"default_factor": 1e400})
    provider = TrafficAdjustedProvider(_FixedDurationProvider(duration_s=300.0), config.traffic)

    matrix = provider.get_matrix(
        origins=[(32.8, -96.7)],
        destinations=[(32.83, -96.77)],
        origin_departure_times=[datetime.now(UTC)],
    )

    adjusted_duration = matrix.durations_seconds[0][0]
    assert adjusted_duration > 0, (
        "an infinite speed_factor divided every real (300s) leg duration down "
        "to 0.0 seconds — the analysis now believes every leg is instantaneous"
    )
