"""Per-leg matrix cache — reuse individual origin->destination legs across runs.

The whole-matrix cache (`cache.py`) only hits when the *exact same fleet* re-runs
at the same date and hour, so two different fleets in the same city share
nothing and each pays Google in full. This caches at the level of a single
**leg** — one (origin, destination) pair at a time-of-day — so any leg a past
run already fetched is free forever after, no matter which fleet it appears in.

Why this is sound, not a corner cut: RouteBench evaluates *planned* routes, not
live navigation. It never needs "traffic right now", only *typical* travel time
at the plan's time of day — and that is stable week to week. So a leg is keyed by
(snapped origin cell, snapped destination cell, weekday-vs-weekend + hour,
traffic profile) and kept for a TTL, rather than pinned to a single timestamp.

Coordinates are snapped to a grid (a few decimal places) so the *same* stop
recurring across fleets — a depot, a repeat customer — reuses its legs even when
the float is not bit-identical. Coarser snapping means more reuse and slightly
more approximation; the precision is a configured dial.

Cost is paid only for legs that are genuinely missing. Fully-uncached origins are
batched into one backend call (their whole row is missing, so no waste);
partially-cached origins fetch exactly their missing destinations. The backend is
billed per element, so this bills exactly the missing-leg count. It sits in the
same place the whole-matrix cache did — beneath the haversine fallback — so an
engine outage still raises straight through to the fallback and nothing
approximate is ever cached.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import structlog

from routebench.core.exceptions import MatrixUnavailableError
from routebench.infra.matrix.base import MatrixProvider, MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# A stored leg: [duration_seconds, distance_meters, written_at_epoch].
_LegEntry = list[float]


class PerLegMatrixCache:
    """Wraps a MatrixProvider, caching and reusing individual legs."""

    name: str = "perleg_cached"

    def __init__(
        self,
        backend: MatrixProvider,
        cache_dir: Path,
        *,
        snap_decimals: int = 4,
        ttl_seconds: float = 7 * 86_400,
        now_epoch: Callable[[], float] = time.time,
    ) -> None:
        self.backend = backend
        self.cache_dir = cache_dir
        self.snap_decimals = snap_decimals
        self.ttl_seconds = ttl_seconds
        self._now_epoch = now_epoch
        self.name = f"perleg_cached_{backend.name}"
        # Per-profile leg tables, loaded lazily and kept in memory. The worker is
        # single-concurrency, so one process owns this and no lock is needed.
        self._mem: dict[str, dict[str, _LegEntry]] = {}

    @property
    def is_time_aware(self) -> bool:
        """Delegated: the cache keys on departure time but adds no awareness of
        its own — a time-aware backend caches per time bucket, a time-agnostic
        one is simply never handed a departure time."""
        return self.backend.is_time_aware

    def profile_hash(self) -> str:
        """Expose the backend's traffic-profile hash, when it has one, so a
        further wrapper can key on it. Present unconditionally to keep the
        MatrixProvider surface duck-type stable."""
        return self._backend_profile_hash() or "noprofile"

    # ------------------------------------------------------------------ helpers

    def _backend_profile_hash(self) -> str | None:
        profile_hash = getattr(self.backend, "profile_hash", None)
        if callable(profile_hash):
            return str(profile_hash())
        return None

    def _snap(self, coord: tuple[float, float]) -> str:
        lat, lon = coord
        return f"{lat:.{self.snap_decimals}f},{lon:.{self.snap_decimals}f}"

    @staticmethod
    def _time_bucket(dt: datetime | None) -> str:
        """Weekday-vs-weekend plus hour. No calendar date: typical travel time at
        08:00 on one Tuesday matches the next, and that reuse is the whole point.
        """
        if dt is None:
            return "na"
        kind = "we" if dt.weekday() >= 5 else "wd"
        return f"{kind}{dt.hour:02d}"

    def _leg_key(self, bucket: str, origin: str, destination: str) -> str:
        return f"{bucket}|{origin}>{destination}"

    def _store_path(self, profile: str) -> Path:
        return self.cache_dir / "perleg" / f"legs__{profile}.json"

    def _load_store(self, profile: str) -> dict[str, _LegEntry]:
        if profile in self._mem:
            return self._mem[profile]
        store: dict[str, _LegEntry] = {}
        path = self._store_path(profile)
        if path.exists():
            horizon = self._now_epoch() - self.ttl_seconds
            try:
                raw = json.loads(path.read_text())
            except (ValueError, OSError):
                logger.warning("perleg_store_unreadable", path=str(path))
                raw = {}
            for key, val in raw.items():
                # Drop entries older than the TTL on load, so a stale file cannot
                # keep serving week-old travel times indefinitely.
                if isinstance(val, list) and len(val) == 3 and float(val[2]) >= horizon:
                    store[key] = [float(val[0]), float(val[1]), float(val[2])]
        self._mem[profile] = store
        return store

    def _save_store(self, profile: str, store: dict[str, _LegEntry]) -> None:
        horizon = self._now_epoch() - self.ttl_seconds
        pruned = {k: v for k, v in store.items() if v[2] >= horizon}
        self._mem[profile] = pruned
        path = self._store_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pruned))
        tmp.replace(path)  # atomic on POSIX

    # -------------------------------------------------------------- main entry

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        n_o, n_d = len(origins), len(destinations)
        profile = self.profile_hash()
        store = self._load_store(profile)
        horizon = self._now_epoch() - self.ttl_seconds

        o_snap = [self._snap(c) for c in origins]
        d_snap = [self._snap(c) for c in destinations]
        # Each origin's leg is driven when that origin is left, so band per origin
        # when the caller supplies a departure vector; otherwise the single plan
        # departure applies to every row.
        o_bucket = [
            self._time_bucket(
                origin_departure_times[i] if origin_departure_times else departure_time
            )
            for i in range(n_o)
        ]

        durations = [[math.nan] * n_d for _ in range(n_o)]
        distances = [[math.nan] * n_d for _ in range(n_o)]
        missing = [[True] * n_d for _ in range(n_o)]
        n_hits = 0
        for i in range(n_o):
            for j in range(n_d):
                entry = store.get(self._leg_key(o_bucket[i], o_snap[i], d_snap[j]))
                if entry is not None and entry[2] >= horizon:
                    durations[i][j] = entry[0]
                    distances[i][j] = entry[1]
                    missing[i][j] = False
                    n_hits += 1

        total = n_o * n_d
        n_missing = total - n_hits
        if n_missing == 0:
            logger.info("perleg_matrix_full_hit", legs=total)
            return MatrixResult(
                durations_seconds=durations,
                distances_meters=distances,
                provider=self.name,
                cached=True,
                cost_estimate=0.0,
                approximate=False,
            )

        fetch_cost = 0.0
        try:
            fetch_cost = self._fetch_missing(
                origins,
                destinations,
                departure_time,
                origin_departure_times,
                missing=missing,
                durations=durations,
                distances=distances,
                store=store,
                o_snap=o_snap,
                d_snap=d_snap,
                o_bucket=o_bucket,
            )
        finally:
            # Persist whatever was fetched even if a later backend call raised,
            # so partial progress survives to the next run rather than being paid
            # for twice.
            self._save_store(profile, store)

        logger.info("perleg_matrix", hits=n_hits, misses=n_missing, legs=total)
        return MatrixResult(
            durations_seconds=durations,
            distances_meters=distances,
            provider=self.name,
            cached=False,
            cost_estimate=fetch_cost,
            approximate=False,
        )

    def _fetch_missing(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None,
        origin_departure_times: list[datetime] | None,
        *,
        missing: list[list[bool]],
        durations: list[list[float]],
        distances: list[list[float]],
        store: dict[str, _LegEntry],
        o_snap: list[str],
        d_snap: list[str],
        o_bucket: list[str],
    ) -> float:
        n_o, n_d = len(origins), len(destinations)
        now = self._now_epoch()
        cost = 0.0

        def _odt(indices: list[int]) -> list[datetime] | None:
            if origin_departure_times is None:
                return None
            return [origin_departure_times[i] for i in indices]

        def _absorb(row_idx: list[int], dest_idx: list[int], res: MatrixResult) -> None:
            # Backends beneath the fallback return exact matrices or raise; an
            # approximate result would poison the cache, so treat it as an engine
            # failure and let the fallback produce the estimate (for the whole
            # matrix, clearly labelled) rather than caching a straight-line guess.
            if res.approximate:
                msg = "backend returned an approximate matrix beneath the cache"
                raise MatrixUnavailableError(msg)
            for local_o, i in enumerate(row_idx):
                for local_d, j in enumerate(dest_idx):
                    sec = res.durations_seconds[local_o][local_d]
                    m = res.distances_meters[local_o][local_d]
                    durations[i][j] = sec
                    distances[i][j] = m
                    store[self._leg_key(o_bucket[i], o_snap[i], d_snap[j])] = [sec, m, now]

        # Origins whose entire row is missing: one batched call over all
        # destinations wastes nothing, because every leg in those rows is new.
        fully_missing = [i for i in range(n_o) if all(missing[i])]
        if fully_missing:
            res = self.backend.get_matrix(
                [origins[i] for i in fully_missing],
                destinations,
                departure_time,
                _odt(fully_missing),
            )
            cost += res.cost_estimate
            _absorb(fully_missing, list(range(n_d)), res)

        # Partially-cached origins: fetch each one's missing destinations exactly,
        # so no already-known leg is paid for again.
        for i in range(n_o):
            if all(missing[i]) or not any(missing[i]):
                continue
            miss_j = [j for j in range(n_d) if missing[i][j]]
            res = self.backend.get_matrix(
                [origins[i]],
                [destinations[j] for j in miss_j],
                departure_time,
                _odt([i]),
            )
            cost += res.cost_estimate
            _absorb([i], miss_j, res)

        return cost
