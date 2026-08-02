"""Adaptive solver budget: solve time scales with problem size, within bounds."""

from __future__ import annotations

import math

from routebench.analysis.benchmark.budget import fleet_time_limit_s, route_time_limit_s
from routebench.core.config import AnalysisConfig


def _cfg(**kw: object) -> AnalysisConfig:
    return AnalysisConfig(**kw)  # type: ignore[arg-type]


class TestAdaptiveOff:
    def test_route_falls_back_to_fixed_limit(self) -> None:
        cfg = _cfg(adaptive_solver_budget=False, route_benchmark_time_limit_s=30)
        assert route_time_limit_s(3, cfg) == 30
        assert route_time_limit_s(50, cfg) == 30

    def test_fleet_falls_back_to_fixed_limit(self) -> None:
        cfg = _cfg(adaptive_solver_budget=False, fleet_benchmark_time_limit_s=120)
        assert fleet_time_limit_s(2, cfg) == 120
        assert fleet_time_limit_s(500, cfg) == 120


class TestAdaptiveRoute:
    def test_scales_with_stops(self) -> None:
        # default 1.5 s/stop
        cfg = _cfg()
        assert route_time_limit_s(10, cfg) == 15

    def test_floors_small_routes(self) -> None:
        cfg = _cfg(route_min_time_limit_s=5, solver_seconds_per_route_stop=1.5)
        assert route_time_limit_s(1, cfg) == 5  # 1.5 rounded is 2, floored to 5

    def test_caps_large_routes(self) -> None:
        cfg = _cfg(route_benchmark_time_limit_s=30, solver_seconds_per_route_stop=1.5)
        assert route_time_limit_s(100, cfg) == 30  # 150 capped to 30


class TestAdaptiveFleet:
    def test_scales_with_total_stops(self) -> None:
        # default 3.0 s/stop
        cfg = _cfg()
        assert fleet_time_limit_s(15, cfg) == 45

    def test_floors_small_fleets(self) -> None:
        cfg = _cfg(fleet_min_time_limit_s=20, solver_seconds_per_fleet_stop=3.0)
        assert fleet_time_limit_s(2, cfg) == 20  # 6 floored to 20

    def test_caps_large_fleets(self) -> None:
        cfg = _cfg(fleet_benchmark_time_limit_s=120, solver_seconds_per_fleet_stop=3.0)
        assert fleet_time_limit_s(80, cfg) == 120  # 240 capped to 120


class TestSharedEnvelope:
    """Many routes share one solver envelope; route count tightens each share
    but never disqualifies a route (routes solve independently)."""

    def test_small_route_count_is_unchanged(self) -> None:
        # A 20-stop route's size-scaled limit is the 30s ceiling. With defaults
        # (8 workers, 180s envelope) the per-route share stays >= 30 up to ~48
        # routes, so those fleets get the exact same budget as before.
        cfg = _cfg()
        assert route_time_limit_s(20, cfg, n_routes=1) == 30
        assert route_time_limit_s(20, cfg, n_routes=8) == 30
        assert route_time_limit_s(20, cfg, n_routes=48) == 30

    def test_large_route_count_shrinks_the_share(self) -> None:
        cfg = _cfg()
        # 64 routes -> ceil(64/8)=8 batches -> 180//8 = 22s each.
        assert route_time_limit_s(20, cfg, n_routes=64) == 22
        # 200 routes -> ceil(200/8)=25 batches -> 180//25 = 7s each.
        assert route_time_limit_s(20, cfg, n_routes=200) == 7

    def test_share_is_floored_not_zeroed(self) -> None:
        cfg = _cfg()
        # A degenerate route count drives the raw share below 1s; floored to 1.
        assert route_time_limit_s(20, cfg, n_routes=5000) == cfg.route_min_floor_s

    def test_wall_clock_stays_within_the_envelope(self) -> None:
        cfg = _cfg()
        for n in (48, 60, 120, 250):
            limit = route_time_limit_s(20, cfg, n_routes=n)
            batches = math.ceil(n / cfg.route_benchmark_workers)
            assert batches * limit <= cfg.route_solver_envelope_s


class TestEndToEndShape:
    def test_small_fleet_is_far_under_the_fixed_ceiling(self) -> None:
        """A 15-stop, 3-route fleet: adaptive is a fraction of the old 3-min run."""
        cfg = _cfg()
        routes = [5, 4, 6]
        route_total = sum(route_time_limit_s(n, cfg) for n in routes)
        fleet = fleet_time_limit_s(sum(routes), cfg)
        # Old fixed cost was 3*30 + 120 = 210s; adaptive is well under a minute of solve.
        assert route_total + fleet < 90
