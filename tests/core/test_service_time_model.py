"""Volume-based service-time model: fit from ground truth, seed as fallback."""

from __future__ import annotations

import pytest

from routebench.core.service_time_model import fit_or_seed


def _line(base: float, per_unit: float, demands: list[float]) -> list[tuple[float, float]]:
    return [(d, base + per_unit * d) for d in demands]


class TestFit:
    def test_learns_a_clean_line(self) -> None:
        # service = 4 + 0.5 * demand, sampled at spread-out demands.
        obs = _line(4.0, 0.5, [2, 10, 18, 26, 34, 42])
        model = fit_or_seed(obs, seed_base=99.0, seed_per_unit=99.0)
        assert model.source == "fitted"
        assert model.base_minutes == pytest.approx(4.0)
        assert model.per_unit_minutes == pytest.approx(0.5)
        assert model.n_observations == 6

    def test_minutes_for_uses_the_fitted_line(self) -> None:
        obs = _line(4.0, 0.5, [2, 10, 18, 26, 34, 42])
        model = fit_or_seed(obs, seed_base=99.0, seed_per_unit=99.0)
        # 4 + 0.5 * 20 = 14.
        assert model.minutes_for(20) == pytest.approx(14.0)


class TestSeedFallback:
    def test_too_few_observations_falls_back_to_seed(self) -> None:
        obs = _line(4.0, 0.5, [2, 10, 18])  # only 3 < the 5 minimum
        model = fit_or_seed(obs, seed_base=5.0, seed_per_unit=0.6)
        assert model.source == "seed"
        assert model.base_minutes == 5.0
        assert model.per_unit_minutes == 0.6
        assert model.n_observations == 3

    def test_no_demand_spread_falls_back_to_seed(self) -> None:
        # Every stop has the same demand — a two-parameter line is meaningless.
        obs = [(20.0, 12.0), (20.0, 15.0), (20.0, 11.0), (20.0, 18.0), (20.0, 14.0)]
        model = fit_or_seed(obs, seed_base=5.0, seed_per_unit=0.6)
        assert model.source == "seed"

    def test_negative_slope_fit_is_rejected(self) -> None:
        # More volume, less time — nonsensical for a service model, use the seed.
        obs = _line(40.0, -0.5, [2, 10, 18, 26, 34, 42])
        model = fit_or_seed(obs, seed_base=5.0, seed_per_unit=0.6)
        assert model.source == "seed"

    def test_negative_intercept_fit_is_rejected(self) -> None:
        # A zero-demand stop taking negative time — reject, use the seed.
        obs = _line(-10.0, 2.0, [8, 10, 18, 26, 34, 42])
        model = fit_or_seed(obs, seed_base=5.0, seed_per_unit=0.6)
        assert model.source == "seed"

    def test_non_finite_and_negative_observations_are_dropped(self) -> None:
        obs: list[tuple[float | None, float | None]] = [
            (2.0, 5.0),
            (float("inf"), 6.0),  # dropped
            (10.0, None),  # dropped
            (-3.0, 7.0),  # dropped (negative demand)
            (18.0, 14.0),
            (26.0, 17.0),
            (34.0, 21.0),
        ]
        model = fit_or_seed(obs, seed_base=5.0, seed_per_unit=0.6)
        # Four usable points remain (< 5) -> seed.
        assert model.n_observations == 4
        assert model.source == "seed"


class TestMinutesFor:
    def test_clamps_negative_and_handles_none(self) -> None:
        model = fit_or_seed([], seed_base=5.0, seed_per_unit=0.6)
        assert model.minutes_for(None) == 5.0  # base only
        assert model.minutes_for(-100) == 5.0  # demand clamped to 0
        assert model.minutes_for(10) == 5.0 + 0.6 * 10
