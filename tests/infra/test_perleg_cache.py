"""Per-leg matrix cache: reuse legs across fleets, pay only for missing ones.

The contract that matters for cost: a leg fetched once is never fetched again
(within the TTL), across any fleet, and a partial overlap fetches exactly the
new legs — not the whole matrix. These tests count what reaches the backend.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from routebench.core.exceptions import MatrixUnavailableError
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.perleg_cache import PerLegMatrixCache

Coord = tuple[float, float]


class _RecordingBackend:
    """Counts elements fetched and returns a unique value per element fetched.

    A monotonic counter as the duration makes reuse visible: a cell served from
    cache keeps its original number, a refetched cell gets a new (higher) one.
    """

    name = "fake"
    is_time_aware = True

    def __init__(self, *, approximate: bool = False) -> None:
        self.calls: list[tuple[int, int]] = []
        self.elements = 0
        self._counter = 0.0
        self._approximate = approximate

    def get_matrix(
        self,
        origins: list[Coord],
        destinations: list[Coord],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        self.calls.append((len(origins), len(destinations)))
        self.elements += len(origins) * len(destinations)
        durs: list[list[float]] = []
        dists: list[list[float]] = []
        for _o in origins:
            row_d: list[float] = []
            row_m: list[float] = []
            for _d in destinations:
                self._counter += 1
                row_d.append(self._counter)
                row_m.append(self._counter * 10)
            durs.append(row_d)
            dists.append(row_m)
        return MatrixResult(
            durations_seconds=durs,
            distances_meters=dists,
            provider="fake",
            cached=False,
            cost_estimate=len(origins) * len(destinations) * 0.01,
            approximate=self._approximate,
        )


class _Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _cache(
    tmp_path: Path, backend: _RecordingBackend, clock: _Clock, **kw: object
) -> PerLegMatrixCache:
    return PerLegMatrixCache(backend=backend, cache_dir=tmp_path / "mc", now_epoch=clock, **kw)


# Distinct coords so snapping does not accidentally merge them.
ORIG = [(40.100, -73.100), (40.200, -73.200), (40.300, -73.300)]
DEST = [(40.400, -73.400), (40.500, -73.500), (40.600, -73.600)]
DEP = datetime(2025, 6, 10, 8, 15)  # a Tuesday, 08:xx


class TestColdAndHit:
    def test_cold_fetches_whole_matrix_in_one_batched_call(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        res = cache.get_matrix(ORIG, DEST, DEP)
        assert backend.elements == 9  # 3x3, all missing
        assert backend.calls == [(3, 3)]  # one batched call, not per-origin
        assert res.approximate is False
        assert res.cached is False

    def test_identical_rerun_is_a_full_hit_no_backend_call(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        first = cache.get_matrix(ORIG, DEST, DEP)
        backend.calls.clear()
        second = cache.get_matrix(ORIG, DEST, DEP)
        assert backend.calls == []  # nothing refetched
        assert second.cached is True
        assert second.cost_estimate == 0.0
        assert second.durations_seconds == first.durations_seconds

    def test_hit_survives_a_fresh_cache_instance_via_disk(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        _cache(tmp_path, backend, _Clock()).get_matrix(ORIG, DEST, DEP)
        # New instance, same dir: the legs must load from disk, not refetch.
        backend2 = _RecordingBackend()
        res = _cache(tmp_path, backend2, _Clock()).get_matrix(ORIG, DEST, DEP)
        assert backend2.elements == 0
        assert res.cached is True


class TestPartialReuse:
    def test_adding_an_origin_fetches_only_its_row(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        cache.get_matrix(ORIG, DEST, DEP)  # 9 elements
        backend.elements = 0
        backend.calls.clear()

        new_origin = (40.400, -73.050)
        cache.get_matrix([*ORIG, new_origin], DEST, DEP)
        # Only the new origin's 3 legs are missing.
        assert backend.elements == 3
        assert backend.calls == [(1, 3)]  # batched fully-missing row

    def test_adding_a_destination_fetches_only_that_column(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        cache.get_matrix(ORIG, DEST, DEP)
        backend.elements = 0
        backend.calls.clear()

        new_dest = (40.700, -73.700)
        cache.get_matrix(ORIG, [*DEST, new_dest], DEP)
        # Each of the 3 origins misses exactly the one new destination.
        assert backend.elements == 3
        # Partial rows fetched per origin, one missing dest each.
        assert backend.calls == [(1, 1), (1, 1), (1, 1)]

    def test_reused_legs_keep_their_original_values(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        first = cache.get_matrix(ORIG, DEST, DEP)
        new_origin = (40.400, -73.050)
        second = cache.get_matrix([*ORIG, new_origin], DEST, DEP)
        # The first three rows are unchanged — served from cache, not refetched.
        assert second.durations_seconds[:3] == first.durations_seconds


class TestSnapping:
    def test_subgrid_precision_difference_still_hits(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock(), snap_decimals=4)
        cache.get_matrix(ORIG, DEST, DEP)
        backend.elements = 0
        # Perturb every coord in the 6th decimal — below 4dp snapping.
        o2 = [(lat + 0.000001, lon - 0.000001) for lat, lon in ORIG]
        d2 = [(lat - 0.000001, lon + 0.000001) for lat, lon in DEST]
        res = cache.get_matrix(o2, d2, DEP)
        assert backend.elements == 0  # snapped to the same cells
        assert res.cached is True

    def test_difference_at_the_snap_decimal_misses(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock(), snap_decimals=4)
        cache.get_matrix(ORIG, DEST, DEP)
        backend.elements = 0
        # Shift by 0.001 — above 4dp, a different cell.
        o2 = [(lat + 0.001, lon) for lat, lon in ORIG]
        cache.get_matrix(o2, DEST, DEP)
        assert backend.elements == 9  # all new cells


class TestTimeBucket:
    def test_same_weekday_and_hour_on_another_date_hits(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        cache.get_matrix(ORIG, DEST, datetime(2025, 6, 10, 8, 15))  # Tue 08
        backend.elements = 0
        cache.get_matrix(ORIG, DEST, datetime(2025, 6, 11, 8, 45))  # Wed 08 -> same bucket
        assert backend.elements == 0

    def test_different_hour_misses(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        cache.get_matrix(ORIG, DEST, datetime(2025, 6, 10, 8, 15))
        backend.elements = 0
        cache.get_matrix(ORIG, DEST, datetime(2025, 6, 10, 9, 15))  # 09 -> new bucket
        assert backend.elements == 9

    def test_weekend_and_weekday_are_separate(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        cache.get_matrix(ORIG, DEST, datetime(2025, 6, 10, 8, 0))  # Tue (weekday)
        backend.elements = 0
        cache.get_matrix(ORIG, DEST, datetime(2025, 6, 14, 8, 0))  # Sat (weekend)
        assert backend.elements == 9


class TestTtl:
    def test_expired_legs_are_refetched(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        clock = _Clock(1_000_000.0)
        cache = _cache(tmp_path, backend, clock, ttl_seconds=100.0)
        cache.get_matrix(ORIG, DEST, DEP)
        backend.elements = 0
        clock.t += 101  # past the TTL
        res = cache.get_matrix(ORIG, DEST, DEP)
        assert backend.elements == 9  # stale -> refetched
        assert res.cached is False

    def test_within_ttl_still_hits(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        clock = _Clock(1_000_000.0)
        cache = _cache(tmp_path, backend, clock, ttl_seconds=100.0)
        cache.get_matrix(ORIG, DEST, DEP)
        backend.elements = 0
        clock.t += 50  # within the TTL
        cache.get_matrix(ORIG, DEST, DEP)
        assert backend.elements == 0


class TestCostAndDegradation:
    def test_cost_reflects_only_fetched_legs(self, tmp_path: Path) -> None:
        backend = _RecordingBackend()
        cache = _cache(tmp_path, backend, _Clock())
        cold = cache.get_matrix(ORIG, DEST, DEP)
        assert cold.cost_estimate == pytest.approx(9 * 0.01)
        new_origin = (40.400, -73.050)
        warm = cache.get_matrix([*ORIG, new_origin], DEST, DEP)
        assert warm.cost_estimate == pytest.approx(3 * 0.01)  # only the new row

    def test_approximate_backend_raises_for_the_fallback(self, tmp_path: Path) -> None:
        backend = _RecordingBackend(approximate=True)
        cache = _cache(tmp_path, backend, _Clock())
        # An approximate result beneath the cache is treated as an engine failure
        # so the fallback (above) produces the estimate — the cache never stores
        # a guess.
        with pytest.raises(MatrixUnavailableError):
            cache.get_matrix(ORIG, DEST, DEP)

    def test_is_time_aware_delegates_to_backend(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, _RecordingBackend(), _Clock())
        assert cache.is_time_aware is True
