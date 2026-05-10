"""Tests for infra/matrix/cache.py — CachedMatrixProvider."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.cache import CachedMatrixProvider, compute_cache_key


class FakeMatrixProvider:
    """A fake matrix provider for testing the cache layer."""

    name: str = "fake"
    call_count: int = 0

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
    ) -> MatrixResult:
        self.call_count += 1
        n_o = len(origins)
        n_d = len(destinations)
        return MatrixResult(
            durations_seconds=[[100.0] * n_d for _ in range(n_o)],
            distances_meters=[[5000.0] * n_d for _ in range(n_o)],
            provider="fake",
            cached=False,
        )


class TestCacheKey:
    """Tests for cache key computation."""

    def test_same_coords_same_key(self) -> None:
        """Same coordinates produce the same cache key."""
        origins = [(32.82500, -96.77500)]
        destinations = [(32.84000, -96.75000)]
        key1 = compute_cache_key(origins, destinations)
        key2 = compute_cache_key(origins, destinations)
        assert key1 == key2

    def test_perturbing_6th_decimal_same_key(self) -> None:
        """Perturbing the 6th decimal does not change the key (rounded to 5)."""
        origins1 = [(32.825001, -96.775001)]
        origins2 = [(32.825004, -96.775004)]
        destinations = [(32.84, -96.75)]
        key1 = compute_cache_key(origins1, destinations)
        key2 = compute_cache_key(origins2, destinations)
        assert key1 == key2

    def test_different_5th_decimal_different_key(self) -> None:
        """Changing the 5th decimal changes the key."""
        origins1 = [(32.82500, -96.77500)]
        origins2 = [(32.82501, -96.77500)]
        destinations = [(32.84, -96.75)]
        key1 = compute_cache_key(origins1, destinations)
        key2 = compute_cache_key(origins2, destinations)
        assert key1 != key2

    def test_departure_time_changes_key(self) -> None:
        """Different departure time buckets produce different keys."""
        origins = [(32.82, -96.77)]
        destinations = [(32.84, -96.75)]
        dt1 = datetime(2025, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        key1 = compute_cache_key(origins, destinations, dt1)
        key2 = compute_cache_key(origins, destinations, dt2)
        assert key1 != key2

    def test_no_departure_time_stable(self) -> None:
        """None departure time produces a stable key."""
        origins = [(32.82, -96.77)]
        destinations = [(32.84, -96.75)]
        key1 = compute_cache_key(origins, destinations, None)
        key2 = compute_cache_key(origins, destinations, None)
        assert key1 == key2


class TestCachedMatrixProvider:
    """Tests for the CachedMatrixProvider wrapper."""

    def test_cache_miss_then_hit(self, tmp_path: Path) -> None:
        """First call hits backend; second call uses cache."""
        fake = FakeMatrixProvider()
        cached = CachedMatrixProvider(backend=fake, cache_dir=tmp_path / "cache")  # type: ignore[arg-type]

        origins = [(32.82, -96.77)]
        destinations = [(32.84, -96.75)]

        # First call: cache miss
        r1 = cached.get_matrix(origins, destinations)
        assert r1.cached is False
        assert fake.call_count == 1

        # Second call: cache hit
        r2 = cached.get_matrix(origins, destinations)
        assert r2.cached is True
        assert fake.call_count == 1  # backend not called again

        # Results should match
        assert r1.durations_seconds == r2.durations_seconds
        assert r1.distances_meters == r2.distances_meters

    def test_different_coords_not_cached(self, tmp_path: Path) -> None:
        """Different coordinates produce different cache entries."""
        fake = FakeMatrixProvider()
        cached = CachedMatrixProvider(backend=fake, cache_dir=tmp_path / "cache")  # type: ignore[arg-type]

        cached.get_matrix([(32.82, -96.77)], [(32.84, -96.75)])
        cached.get_matrix([(32.90, -96.70)], [(32.95, -96.65)])

        assert fake.call_count == 2
