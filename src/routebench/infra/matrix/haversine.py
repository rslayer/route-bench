"""Offline fallback matrix provider: straight-line estimates, no network.

This exists so the service is genuinely standalone. OSRM is the only source of
real road-network travel times, and when it is unreachable every upload used to
be accepted with a 202 and then die at 15% with "Could not connect to OSRM" —
the whole site down behind a healthy-looking front door.

The estimates here are deliberately crude and openly labelled. `approximate` is
set on every result, and the pipeline withholds the quality grade when it sees
that flag: showing a user their routes, their map, and their relative findings
on estimated times is useful, but grading route *quality* on guessed travel
times would be a number that looks authoritative and is not.

This mirrors the geometry path, which already degrades this way — an
unreachable OSRM there falls back to `straight_line()` tagged `approximate`
rather than failing the render.
"""

from __future__ import annotations

import math
from datetime import datetime

import structlog

from routebench.infra.matrix.base import MatrixResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

EARTH_RADIUS_METERS = 6_371_000.0

# Straight-line distance understates road distance: streets bend, rivers need
# bridges, and one-ways double back. 1.3 is the common planar-network detour
# ratio for urban road networks — close enough for an estimate that is labelled
# as an estimate, and it errs toward realistic rather than flattering.
DEFAULT_DETOUR_FACTOR = 1.3

# A blended urban/suburban delivery speed. Not a claim about any specific
# network — just a defensible constant so the estimate lands in the right order
# of magnitude rather than assuming free-flow highway speed everywhere.
DEFAULT_SPEED_KPH = 35.0


def _haversine_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in meters between two (lat, lon) points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2.0 * EARTH_RADIUS_METERS * math.asin(math.sqrt(h))


class HaversineMatrixProvider:
    """Estimates a travel-time matrix from straight-line distance.

    Needs no network and never raises, which is the point: it is the floor that
    keeps the service answering when the routing backend is gone.
    """

    name = "haversine"

    def __init__(
        self,
        speed_kph: float = DEFAULT_SPEED_KPH,
        detour_factor: float = DEFAULT_DETOUR_FACTOR,
    ) -> None:
        if speed_kph <= 0:
            msg = f"speed_kph must be positive, got {speed_kph}"
            raise ValueError(msg)
        if detour_factor < 1.0:
            msg = f"detour_factor must be at least 1.0, got {detour_factor}"
            raise ValueError(msg)
        self._speed_mps = speed_kph * 1000.0 / 3600.0
        self._detour_factor = detour_factor

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        """Estimated durations and distances for every origin/destination pair.

        Time-of-day arguments are accepted and ignored: this models no traffic
        variation, and pretending otherwise would dress a guess up as a
        forecast. A caller that wants banding wraps this in
        TrafficAdjustedProvider, exactly as it would wrap OSRM.
        """
        distances: list[list[float]] = []
        durations: list[list[float]] = []

        for origin in origins:
            row_distances: list[float] = []
            row_durations: list[float] = []
            for destination in destinations:
                meters = _haversine_meters(origin, destination) * self._detour_factor
                row_distances.append(meters)
                row_durations.append(meters / self._speed_mps)
            distances.append(row_distances)
            durations.append(row_durations)

        logger.warning(
            "haversine_matrix_estimated",
            n_origins=len(origins),
            n_destinations=len(destinations),
            reason="routing backend unavailable; travel times are straight-line estimates",
        )

        return MatrixResult(
            durations_seconds=durations,
            distances_meters=distances,
            provider=self.name,
            cached=False,
            approximate=True,
        )
