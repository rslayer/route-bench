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
    profile_hash: str | None = None,
    origin_departure_times: list[datetime] | None = None,
) -> str:
    """Compute a stable cache key from coordinates and departure time.

    When this cache wraps a traffic-adjusted provider it stores banded matrices,
    so the key must separate them: `profile_hash` keeps two profiles apart, and
    `origin_departure_times` keeps two runs of the same profile apart (identical
    coordinates departing at different times band differently).

    Both are keyed in only when present, so free-flow keys are unchanged.
    """
    rounded_origins = _round_coords(origins)
    rounded_destinations = _round_coords(destinations)
    payload: dict[str, object] = {
        "origins": rounded_origins,
        "destinations": rounded_destinations,
        "time_bucket": _time_bucket(departure_time),
    }
    if profile_hash is not None:
        payload["profile_hash"] = profile_hash
    if origin_departure_times:
        payload["origin_departure_times"] = [dt.isoformat() for dt in origin_departure_times]
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
        # Created on first write, not here. Constructing the app should not
        # touch the filesystem: create_app runs in every test and would leave a
        # cache directory in whatever the working directory happened to be,
        # and a deployment that never fetches a matrix has no reason to have one.
        self.name = f"cached_{backend.name}"

    @property
    def is_time_aware(self) -> bool:
        """Reflects what is being cached — the cache adds no time awareness of
        its own. It DOES key on departure_time, so a time-aware backend caches
        correctly per time; a time-agnostic one is simply never handed one."""
        return self.backend.is_time_aware

    def _backend_profile_hash(self) -> str | None:
        """Profile hash of the wrapped backend, if it applies a traffic profile.

        Duck-typed rather than imported: `infra.matrix.cache` should not depend
        on the traffic layer to cache correctly when composed beneath it.
        """
        profile_hash = getattr(self.backend, "profile_hash", None)
        if callable(profile_hash):
            return str(profile_hash())
        return None

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        """Get matrix from cache or backend.

        Returns a cached result if available; otherwise queries the backend,
        caches the result, and returns it.
        """
        key = compute_cache_key(
            origins,
            destinations,
            departure_time,
            profile_hash=self._backend_profile_hash(),
            origin_departure_times=origin_departure_times,
        )
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
                # Defaults False, which is only safe because approximate results
                # are never written (below). Read back explicitly anyway: an
                # entry written by an older build predates that rule, and a
                # cached estimate returning as exact would republish the grade
                # on estimated times — the precise outcome grade-withholding
                # exists to prevent.
                approximate=data.get("approximate", False),
            )

        logger.info("matrix_cache_miss", key=key[:12])
        result = self.backend.get_matrix(
            origins, destinations, departure_time, origin_departure_times
        )

        if result.approximate:
            # Deliberately not cached. The cache exists to spare the real
            # routing engine, and an approximate result means that engine was
            # down. Persisting the fallback would let a few seconds of OSRM
            # downtime serve haversine estimates for the whole cache lifetime,
            # long after OSRM recovered, with no signal that anything was wrong.
            # Better to re-ask and get the real answer once it can.
            logger.info("matrix_cache_skip_approximate", key=key[:12], provider=result.provider)
            return result

        cache_data = {
            "durations_seconds": result.durations_seconds,
            "distances_meters": result.distances_meters,
            "provider": result.provider,
            "cost_estimate": result.cost_estimate,
            "approximate": result.approximate,
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache_data))

        return MatrixResult(
            durations_seconds=result.durations_seconds,
            distances_meters=result.distances_meters,
            provider=result.provider,
            cached=False,
            cost_estimate=result.cost_estimate,
            approximate=result.approximate,
        )
