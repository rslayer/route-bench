"""Tests for traffic-adjusted travel times.

Covers band lookup, factor application, provider composition, and cache-key
separation. The regression test proving a profile moves compliance findings
while leaving distances untouched lives in tests/analysis/test_traffic.py.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from routebench.core.config import (
    NAMED_TRAFFIC_PROFILES,
    AnalysisConfig,
    TrafficBand,
    TrafficProfile,
)
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.cache import CachedMatrixProvider, compute_cache_key
from routebench.infra.matrix.traffic import TrafficAdjustedProvider, apply_row_factors


def _profile() -> TrafficProfile:
    """A two-band profile mirroring urban_us."""
    return TrafficProfile(
        bands=[
            TrafficBand(start=time(7, 0), end=time(9, 0), speed_factor=0.75),
            TrafficBand(start=time(16, 0), end=time(18, 30), speed_factor=0.80),
        ]
    )


def _matrix(n: int, duration_s: float = 100.0, distance_m: float = 5000.0) -> MatrixResult:
    return MatrixResult(
        durations_seconds=[[duration_s] * n for _ in range(n)],
        distances_meters=[[distance_m] * n for _ in range(n)],
        provider="fake",
        cached=False,
    )


class FakeProvider:
    """Counts backend fetches so we can prove the two-pass flow stays cheap."""

    name: str = "fake"

    def __init__(self, n: int = 3, duration_s: float = 100.0) -> None:
        self.call_count = 0
        self._n = n
        self._duration_s = duration_s

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        self.call_count += 1
        return _matrix(len(origins), self._duration_s)


def _coords(n: int) -> list[tuple[float, float]]:
    return [(32.80 + 0.01 * i, -96.80) for i in range(n)]


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2025, 1, 15, hour, minute, tzinfo=UTC)


class TestTrafficBand:
    """Band construction rules."""

    def test_rejects_start_after_end(self) -> None:
        with pytest.raises(ValueError, match="start must precede end"):
            TrafficBand(start=time(18, 0), end=time(9, 0), speed_factor=0.8)

    def test_rejects_zero_length_band(self) -> None:
        with pytest.raises(ValueError, match="start must precede end"):
            TrafficBand(start=time(9, 0), end=time(9, 0), speed_factor=0.8)

    def test_rejects_non_positive_speed_factor(self) -> None:
        with pytest.raises(ValueError):
            TrafficBand(start=time(7, 0), end=time(9, 0), speed_factor=0.0)


class TestBandLookup:
    """factor_at resolves a wall-clock time to a speed factor."""

    def test_inside_band(self) -> None:
        assert _profile().factor_at(time(7, 30)) == 0.75

    def test_band_start_is_inclusive(self) -> None:
        assert _profile().factor_at(time(7, 0)) == 0.75

    def test_band_end_is_exclusive(self) -> None:
        assert _profile().factor_at(time(9, 0)) == 1.0

    def test_outside_all_bands_uses_default(self) -> None:
        assert _profile().factor_at(time(12, 0)) == 1.0

    def test_second_band(self) -> None:
        assert _profile().factor_at(time(17, 0)) == 0.80

    def test_custom_default_factor_applies_outside_bands(self) -> None:
        profile = TrafficProfile(bands=_profile().bands, default_factor=0.9)
        assert profile.factor_at(time(12, 0)) == 0.9
        assert profile.factor_at(time(7, 30)) == 0.75

    def test_empty_profile_is_identity(self) -> None:
        profile = TrafficProfile()
        assert profile.factor_at(time(7, 30)) == 1.0
        assert profile.is_active is False

    def test_uniform_slowdown_is_active_without_bands(self) -> None:
        assert TrafficProfile(default_factor=0.9).is_active is True

    def test_overlapping_bands_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not overlap"):
            TrafficProfile(
                bands=[
                    TrafficBand(start=time(7, 0), end=time(9, 0), speed_factor=0.75),
                    TrafficBand(start=time(8, 0), end=time(10, 0), speed_factor=0.80),
                ]
            )

    def test_abutting_bands_allowed(self) -> None:
        profile = TrafficProfile(
            bands=[
                TrafficBand(start=time(7, 0), end=time(9, 0), speed_factor=0.75),
                TrafficBand(start=time(9, 0), end=time(11, 0), speed_factor=0.80),
            ]
        )
        assert profile.factor_at(time(9, 0)) == 0.80


class TestNamedProfiles:
    """The shipped urban_us profile and config resolution."""

    def test_urban_us_values(self) -> None:
        profile = NAMED_TRAFFIC_PROFILES["urban_us"]
        assert profile.factor_at(time(7, 30)) == 0.75
        assert profile.factor_at(time(17, 0)) == 0.80
        assert profile.factor_at(time(12, 0)) == 1.0
        assert profile.factor_at(time(18, 30)) == 1.0

    def test_config_resolves_profile_by_name(self) -> None:
        config = AnalysisConfig.model_validate({"traffic": "urban_us"})
        assert config.traffic.is_active
        assert config.traffic.factor_at(time(7, 30)) == 0.75

    def test_config_rejects_unknown_name(self) -> None:
        with pytest.raises(ValueError, match="Unknown traffic profile"):
            AnalysisConfig.model_validate({"traffic": "mars_rush_hour"})

    def test_config_default_is_free_flow(self) -> None:
        assert AnalysisConfig().traffic.is_active is False

    def test_named_resolution_does_not_share_singleton(self) -> None:
        config = AnalysisConfig.model_validate({"traffic": "urban_us"})
        config.traffic.bands.clear()
        assert NAMED_TRAFFIC_PROFILES["urban_us"].bands, "shipped profile was mutated"


class TestProfileHash:
    """Hashes separate cache entries, so equal profiles must agree."""

    def test_identical_profiles_match(self) -> None:
        assert _profile().profile_hash() == _profile().profile_hash()

    def test_band_order_does_not_matter(self) -> None:
        reversed_bands = TrafficProfile(bands=list(reversed(_profile().bands)))
        assert reversed_bands.profile_hash() == _profile().profile_hash()

    def test_different_factor_differs(self) -> None:
        other = TrafficProfile(
            bands=[TrafficBand(start=time(7, 0), end=time(9, 0), speed_factor=0.5)]
        )
        assert other.profile_hash() != _profile().profile_hash()

    def test_different_default_factor_differs(self) -> None:
        a = TrafficProfile(bands=_profile().bands, default_factor=1.0)
        b = TrafficProfile(bands=_profile().bands, default_factor=0.9)
        assert a.profile_hash() != b.profile_hash()


class TestApplyRowFactors:
    """Row-wise duration scaling."""

    def test_divides_durations_by_row_factor(self) -> None:
        adjusted = apply_row_factors(_matrix(2, duration_s=100.0), [0.5, 1.0])
        assert adjusted.durations_seconds[0] == pytest.approx([200.0, 200.0])
        assert adjusted.durations_seconds[1] == pytest.approx([100.0, 100.0])

    def test_distances_untouched(self) -> None:
        base = _matrix(2)
        adjusted = apply_row_factors(base, [0.5, 0.75])
        assert adjusted.distances_meters == base.distances_meters

    def test_unreachable_legs_stay_unreachable(self) -> None:
        base = MatrixResult(
            durations_seconds=[[math.inf, 100.0]],
            distances_meters=[[math.inf, 5000.0]],
            provider="fake",
            cached=False,
        )
        adjusted = apply_row_factors(base, [0.75])
        assert math.isinf(adjusted.durations_seconds[0][0])

    def test_rejects_factor_count_mismatch(self) -> None:
        with pytest.raises(ValueError, match="one speed factor per origin"):
            apply_row_factors(_matrix(3), [0.75, 0.80])


class TestTrafficAdjustedProvider:
    """Provider composition and banding."""

    def test_bands_each_row_by_its_origin_departure(self) -> None:
        backend = FakeProvider()
        provider = TrafficAdjustedProvider(backend, _profile())

        result = provider.get_matrix(
            _coords(3),
            _coords(3),
            origin_departure_times=[_at(7, 30), _at(12, 0), _at(17, 0)],
        )

        # 100s free-flow: 0.75x speed -> 133.3s, 1.0x -> 100s, 0.80x -> 125s
        assert result.durations_seconds[0][0] == pytest.approx(133.333, rel=1e-3)
        assert result.durations_seconds[1][0] == pytest.approx(100.0)
        assert result.durations_seconds[2][0] == pytest.approx(125.0)

    def test_no_departure_times_returns_free_flow(self) -> None:
        provider = TrafficAdjustedProvider(FakeProvider(), _profile())
        result = provider.get_matrix(_coords(3), _coords(3))
        assert result.durations_seconds[0][0] == pytest.approx(100.0)

    def test_inactive_profile_is_identity(self) -> None:
        provider = TrafficAdjustedProvider(FakeProvider(), TrafficProfile())
        result = provider.get_matrix(
            _coords(2), _coords(2), origin_departure_times=[_at(7, 30), _at(7, 30)]
        )
        assert result.durations_seconds[0][0] == pytest.approx(100.0)

    def test_distances_survive_banding(self) -> None:
        provider = TrafficAdjustedProvider(FakeProvider(), _profile())
        result = provider.get_matrix(
            _coords(2), _coords(2), origin_departure_times=[_at(7, 30), _at(7, 30)]
        )
        assert result.distances_meters == [[5000.0, 5000.0], [5000.0, 5000.0]]

    def test_rejects_departure_time_count_mismatch(self) -> None:
        provider = TrafficAdjustedProvider(FakeProvider(), _profile())
        with pytest.raises(ValueError, match="one departure time per origin"):
            provider.get_matrix(_coords(3), _coords(3), origin_departure_times=[_at(7, 30)])

    def test_two_pass_flow_fetches_backend_once(self) -> None:
        """The free-flow pass and the banded pass share one backend fetch."""
        backend = FakeProvider()
        provider = TrafficAdjustedProvider(backend, _profile())
        coords = _coords(3)

        provider.get_matrix(coords, coords)
        provider.get_matrix(
            coords, coords, origin_departure_times=[_at(7, 30), _at(7, 40), _at(7, 50)]
        )

        assert backend.call_count == 1

    def test_distinct_coords_still_fetch(self) -> None:
        backend = FakeProvider()
        provider = TrafficAdjustedProvider(backend, _profile())
        provider.get_matrix(_coords(3), _coords(3))
        provider.get_matrix(_coords(4), _coords(4))
        assert backend.call_count == 2

    def test_exposes_profile_hash(self) -> None:
        provider = TrafficAdjustedProvider(FakeProvider(), _profile())
        assert provider.profile_hash() == _profile().profile_hash()


class TestTrafficProperties:
    """Property: slowing traffic can never make a leg finish sooner."""

    @given(
        durations=st.lists(
            st.floats(min_value=0.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=8,
        ),
        factors=st.lists(
            st.floats(min_value=0.05, max_value=1.0, allow_nan=False),
            min_size=1,
            max_size=8,
        ),
    )
    @settings(max_examples=200)
    def test_adjusted_never_faster_than_free_flow(
        self, durations: list[float], factors: list[float]
    ) -> None:
        n = min(len(durations), len(factors))
        base = MatrixResult(
            durations_seconds=[[durations[i]] for i in range(n)],
            distances_meters=[[1000.0] for _ in range(n)],
            provider="fake",
            cached=False,
        )
        adjusted = apply_row_factors(base, factors[:n])
        for i in range(n):
            assert adjusted.durations_seconds[i][0] >= base.durations_seconds[i][0]

    @given(
        hour=st.integers(min_value=0, max_value=23),
        minute=st.integers(min_value=0, max_value=59),
    )
    def test_factor_always_resolves_to_a_known_value(self, hour: int, minute: int) -> None:
        factor = _profile().factor_at(time(hour, minute))
        assert factor in {0.75, 0.80, 1.0}


class TestCacheKeySeparation:
    """A cache sitting above the traffic layer must not blend profiles."""

    def test_different_profile_hash_different_key(self) -> None:
        origins, dests = _coords(2), _coords(2)
        key_a = compute_cache_key(origins, dests, profile_hash="aaaa")
        key_b = compute_cache_key(origins, dests, profile_hash="bbbb")
        assert key_a != key_b

    def test_same_profile_hash_same_key(self) -> None:
        origins, dests = _coords(2), _coords(2)
        assert compute_cache_key(origins, dests, profile_hash="aaaa") == compute_cache_key(
            origins, dests, profile_hash="aaaa"
        )

    def test_free_flow_key_unchanged_by_profile_support(self) -> None:
        """Adding profile support must not invalidate existing free-flow entries."""
        origins, dests = _coords(2), _coords(2)
        assert compute_cache_key(origins, dests) == compute_cache_key(
            origins, dests, profile_hash=None, origin_departure_times=None
        )

    def test_different_departure_times_different_key(self) -> None:
        """Same profile, different departure times band differently."""
        origins, dests = _coords(2), _coords(2)
        key_a = compute_cache_key(
            origins, dests, profile_hash="aaaa", origin_departure_times=[_at(7, 30), _at(7, 40)]
        )
        key_b = compute_cache_key(
            origins, dests, profile_hash="aaaa", origin_departure_times=[_at(12, 0), _at(12, 10)]
        )
        assert key_a != key_b

    def test_cache_above_traffic_separates_profiles(self, tmp_path: Path) -> None:
        """Two profiles over identical coords must not serve each other's matrices."""
        coords = _coords(2)
        departures = [_at(7, 30), _at(7, 40)]

        slow = CachedMatrixProvider(
            TrafficAdjustedProvider(FakeProvider(), _profile()), tmp_path / "c"
        )
        fast = CachedMatrixProvider(
            TrafficAdjustedProvider(
                FakeProvider(),
                TrafficProfile(
                    bands=[TrafficBand(start=time(7, 0), end=time(9, 0), speed_factor=0.5)]
                ),
            ),
            tmp_path / "c",
        )

        slow_result = slow.get_matrix(coords, coords, origin_departure_times=departures)
        fast_result = fast.get_matrix(coords, coords, origin_departure_times=departures)

        assert slow_result.durations_seconds[0][0] == pytest.approx(133.333, rel=1e-3)
        assert fast_result.durations_seconds[0][0] == pytest.approx(200.0)

    def test_cache_without_traffic_backend_has_no_profile_hash(self, tmp_path: Path) -> None:
        cache = CachedMatrixProvider(FakeProvider(), tmp_path / "c")
        assert cache._backend_profile_hash() is None
