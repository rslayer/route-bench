"""The service must survive its routing backend going away.

Before this, an unreachable OSRM meant every upload was accepted with a 202 and
then died at 15% with "Could not connect to OSRM" — a healthy front door on a
dead service. These pin the degradation path that replaced it.
"""

from __future__ import annotations

import math

import pytest

from routebench.core.exceptions import MatrixUnavailableError
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.fallback import FallbackMatrixProvider
from routebench.infra.matrix.haversine import HaversineMatrixProvider

# Dallas-ish points roughly 3.4 km apart.
_A = (32.7767, -96.7970)
_B = (32.8000, -96.8000)


class _AlwaysFails:
    name = "boom"

    def get_matrix(self, origins, destinations, departure_time=None, origin_departure_times=None):
        msg = "Could not connect to OSRM"
        raise MatrixUnavailableError(msg, provider="osrm")


class _RaisesOther:
    name = "buggy"

    def get_matrix(self, origins, destinations, departure_time=None, origin_departure_times=None):
        msg = "a genuine bug, not an outage"
        raise ValueError(msg)


class _Works:
    name = "real"

    def get_matrix(self, origins, destinations, departure_time=None, origin_departure_times=None):
        n_o, n_d = len(origins), len(destinations)
        return MatrixResult(
            durations_seconds=[[111.0] * n_d for _ in range(n_o)],
            distances_meters=[[222.0] * n_d for _ in range(n_o)],
            provider="real",
            cached=False,
        )


class TestHaversineProvider:
    def test_marks_itself_approximate(self) -> None:
        """The flag is the whole contract: downstream withholds the grade on it."""
        r = HaversineMatrixProvider().get_matrix([_A], [_B])
        assert r.approximate is True
        assert r.provider == "haversine"

    def test_matrix_shape_matches_inputs(self) -> None:
        r = HaversineMatrixProvider().get_matrix([_A, _B, _A], [_B, _A])
        assert len(r.durations_seconds) == 3
        assert all(len(row) == 2 for row in r.durations_seconds)
        assert len(r.distances_meters) == 3

    def test_self_distance_is_zero(self) -> None:
        r = HaversineMatrixProvider().get_matrix([_A], [_A])
        assert r.distances_meters[0][0] == pytest.approx(0.0, abs=1e-6)
        assert r.durations_seconds[0][0] == pytest.approx(0.0, abs=1e-6)

    def test_distance_is_in_the_right_ballpark(self) -> None:
        """~2.6 km straight line, x1.3 detour => ~3.4 km. Not exact — it is an
        estimate — but it must not be off by an order of magnitude."""
        r = HaversineMatrixProvider().get_matrix([_A], [_B])
        meters = r.distances_meters[0][0]
        assert 2_000 < meters < 6_000, f"implausible estimate: {meters:.0f} m"

    def test_duration_follows_the_configured_speed(self) -> None:
        r = HaversineMatrixProvider(speed_kph=36.0, detour_factor=1.0).get_matrix([_A], [_B])
        meters, seconds = r.distances_meters[0][0], r.durations_seconds[0][0]
        assert seconds == pytest.approx(meters / 10.0, rel=1e-6)  # 36 kph == 10 m/s

    def test_detour_factor_inflates_distance(self) -> None:
        plain = HaversineMatrixProvider(detour_factor=1.0).get_matrix([_A], [_B])
        detoured = HaversineMatrixProvider(detour_factor=1.5).get_matrix([_A], [_B])
        assert detoured.distances_meters[0][0] == pytest.approx(
            plain.distances_meters[0][0] * 1.5, rel=1e-9
        )

    def test_never_produces_nan_or_inf(self) -> None:
        """Antipodal and identical points are the classic haversine edge cases;
        a NaN here would poison every downstream metric."""
        pts = [(0.0, 0.0), (0.0, 180.0), (90.0, 0.0), (-90.0, 0.0), _A]
        r = HaversineMatrixProvider().get_matrix(pts, pts)
        for row in r.durations_seconds + r.distances_meters:
            for v in row:
                assert math.isfinite(v), f"non-finite value {v}"

    def test_rejects_nonsense_configuration(self) -> None:
        with pytest.raises(ValueError):
            HaversineMatrixProvider(speed_kph=0)
        with pytest.raises(ValueError):
            HaversineMatrixProvider(detour_factor=0.5)


class TestFallbackProvider:
    def test_uses_primary_when_it_works(self) -> None:
        p = FallbackMatrixProvider(_Works(), HaversineMatrixProvider())
        r = p.get_matrix([_A], [_B])
        assert r.provider == "real"
        assert r.approximate is False

    def test_falls_back_when_primary_is_unavailable(self) -> None:
        p = FallbackMatrixProvider(_AlwaysFails(), HaversineMatrixProvider())
        r = p.get_matrix([_A], [_B])
        assert r.provider == "haversine"
        assert r.approximate is True

    def test_does_not_swallow_non_availability_errors(self) -> None:
        """A bug in our own request building must not be laundered into
        plausible-looking estimates — only an outage triggers the fallback."""
        p = FallbackMatrixProvider(_RaisesOther(), HaversineMatrixProvider())
        with pytest.raises(ValueError):
            p.get_matrix([_A], [_B])
