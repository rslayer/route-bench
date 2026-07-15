"""Traffic-adjusted MatrixProvider wrapper.

Wraps any MatrixProvider and slows (or speeds) its free-flow durations using
time-banded speed multipliers. Distances are never touched.

Banding is per origin, not per cell: entry (i, j) is the leg from origins[i] to
destinations[j], and that leg is driven starting when the vehicle leaves
origins[i]. So every cell in row i shares one factor. This keeps the factor
well-defined for legs the plan never drives — which matters because the VRPTW
solver evaluates cells outside the planned sequence.

Departure times are supplied by the caller rather than derived here: propagating
them needs service times, time windows, and work rules, which live in `analysis`.
See analysis.scoring.time.compute_departure_schedule.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import structlog

from routebench.core.config import TrafficProfile
from routebench.infra.matrix.base import MatrixProvider, MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def apply_row_factors(matrix: MatrixResult, factors: list[float]) -> MatrixResult:
    """Divide each duration row by its origin's speed factor.

    Dividing by the factor converts a speed multiplier into a duration
    multiplier: at 0.75x speed a leg takes 1/0.75 = 1.33x as long. Unreachable
    legs (inf) stay inf. Distances pass through untouched.

    Raises:
        ValueError: If `factors` does not have exactly one entry per row.
    """
    if len(factors) != len(matrix.durations_seconds):
        msg = (
            f"Expected one speed factor per origin: got {len(factors)} factors "
            f"for {len(matrix.durations_seconds)} rows"
        )
        raise ValueError(msg)

    adjusted = [
        [duration / factor for duration in row]
        for row, factor in zip(matrix.durations_seconds, factors, strict=True)
    ]

    return MatrixResult(
        durations_seconds=adjusted,
        distances_meters=matrix.distances_meters,
        provider=f"traffic_adjusted({matrix.provider})",
        cached=matrix.cached,
        cost_estimate=matrix.cost_estimate,
    )


def _memo_key(
    origins: list[tuple[float, float]],
    destinations: list[tuple[float, float]],
    departure_time: datetime | None,
) -> str:
    payload = {
        "origins": [(round(lat, 5), round(lon, 5)) for lat, lon in origins],
        "destinations": [(round(lat, 5), round(lon, 5)) for lat, lon in destinations],
        "departure_time": departure_time.isoformat() if departure_time else None,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class TrafficAdjustedProvider:
    """Applies a TrafficProfile on top of any MatrixProvider's free-flow times."""

    name: str = "traffic_adjusted"

    def __init__(self, backend: MatrixProvider, profile: TrafficProfile) -> None:
        self.backend = backend
        self.profile = profile
        self.name = f"traffic_{backend.name}"
        # Callers need a free-flow pass to estimate departure times before they
        # can ask for a banded matrix over the same coordinates. Memoizing the
        # backend result keeps that two-pass flow to a single network fetch.
        # Scoped to this instance, which is built per session.
        self._base_memo: dict[str, MatrixResult] = {}

    def profile_hash(self) -> str:
        """Expose the profile hash so a wrapping cache can key on it."""
        return self.profile.profile_hash()

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        """Return the backend matrix with durations banded by departure time.

        With no `origin_departure_times` there is no basis on which to band any
        leg, so the backend's free-flow matrix is returned unchanged. Callers
        use exactly this to obtain the free-flow pass they need in order to
        estimate departure times in the first place.
        """
        base = self._fetch_base(origins, destinations, departure_time)

        if not self.profile.is_active:
            return base

        if origin_departure_times is None:
            logger.debug(
                "traffic_unbanded_no_departure_times",
                provider=self.name,
                n_origins=len(origins),
            )
            return base

        if len(origin_departure_times) != len(origins):
            msg = (
                f"Expected one departure time per origin: got "
                f"{len(origin_departure_times)} for {len(origins)} origins"
            )
            raise ValueError(msg)

        factors = [self.profile.factor_at(dt.time()) for dt in origin_departure_times]
        logger.debug(
            "traffic_bands_applied",
            provider=self.name,
            profile_hash=self.profile_hash(),
            n_slowed=sum(1 for f in factors if f < 1.0),
            n_origins=len(origins),
        )
        return apply_row_factors(base, factors)

    def _fetch_base(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None,
    ) -> MatrixResult:
        key = _memo_key(origins, destinations, departure_time)
        memoized = self._base_memo.get(key)
        if memoized is not None:
            return memoized

        result = self.backend.get_matrix(origins, destinations, departure_time)
        self._base_memo[key] = result
        return result
