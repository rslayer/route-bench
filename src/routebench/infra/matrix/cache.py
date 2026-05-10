"""Cached MatrixProvider wrapper.

Cache key is a stable hash of rounded-to-5-decimal coordinate pairs
and the departure time bucket. Backend is local filesystem in v1.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import structlog

from routebench.infra.matrix.base import MatrixProvider, MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _round_coords(
    coords: list[tuple[float, float]], decimals: int = 5
) -> list[tuple[float, float]]:
    """Round coordinate pairs to the specified number of decimal places."""
    return [(round(lat, decimals), round(lon, decimals)) for lat, lon in coords]


def _time_bucket(dt: datetime | None) -> str:
    """Bucket a departure time to the nearest hour for cache keying."""
    if dt is None:
        return "none"
    return dt.strftime("%Y%m%d_%H")


def compute_cache_key(
    origins: list[tuple[float, float]],
    destinations: list[tuple[float, float]],
    departure_time: datetime | None = None,
) -> str:
    """Compute a stable cache key from coordinates and departure time."""
    rounded_origins = _round_coords(origins)
    rounded_destinations = _round_coords(destinations)
    payload = {
        "origins": rounded_origins,
        "destinations": rounded_destinations,
        "time_bucket": _time_bucket(departure_time),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class CachedMatrixProvider:
    """Wraps any MatrixProvider with local filesystem caching.

    One file per cache key under cache_dir, using a content-addressed scheme.
    """

    name: str = "cached"

    def __init__(self, backend: MatrixProvider, cache_dir: Path) -> None:
        self.backend = backend
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.name = f"cached_{backend.name}"

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
    ) -> MatrixResult:
        """Get matrix from cache or backend.

        Returns a cached result if available; otherwise queries the backend,
        caches the result, and returns it.
        """
        key = compute_cache_key(origins, destinations, departure_time)
        cache_path = self.cache_dir / f"{key}.json"

        if cache_path.exists():
            logger.info("matrix_cache_hit", key=key[:12])
            data = json.loads(cache_path.read_text())
            return MatrixResult(
                durations_seconds=data["durations_seconds"],
                distances_meters=data["distances_meters"],
                provider=data["provider"],
                cached=True,
                cost_estimate=data.get("cost_estimate", 0.0),
            )

        logger.info("matrix_cache_miss", key=key[:12])
        result = self.backend.get_matrix(origins, destinations, departure_time)

        # Write to cache
        cache_data = {
            "durations_seconds": result.durations_seconds,
            "distances_meters": result.distances_meters,
            "provider": result.provider,
            "cost_estimate": result.cost_estimate,
        }
        cache_path.write_text(json.dumps(cache_data))

        return MatrixResult(
            durations_seconds=result.durations_seconds,
            distances_meters=result.distances_meters,
            provider=result.provider,
            cached=False,
            cost_estimate=result.cost_estimate,
        )
