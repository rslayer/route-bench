"""MatrixProvider protocol and MatrixResult model."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import numpy as np
from pydantic import BaseModel


class MatrixResult(BaseModel):
    """Result of an origin-destination matrix query.

    Stores durations and distances as nested lists (Pydantic-friendly).
    Use durations_array() / distances_array() for NumPy access.
    """

    durations_seconds: list[list[float]]
    distances_meters: list[list[float]]
    provider: str
    cached: bool
    cost_estimate: float = 0.0
    # True when these numbers are estimated rather than derived from the road
    # network — a straight-line fallback used because the routing service was
    # unreachable. Callers must be able to tell the difference: an approximate
    # matrix is fine for showing a user their routes and relative findings, but
    # it is not a basis for grading route quality, so the pipeline withholds the
    # grade rather than publishing one computed from guessed travel times.
    approximate: bool = False

    def durations_array(self) -> np.ndarray[tuple[int, int], np.dtype[np.float64]]:
        """Return durations as a NumPy 2D array."""
        return np.asarray(self.durations_seconds, dtype=np.float64)

    def distances_array(self) -> np.ndarray[tuple[int, int], np.dtype[np.float64]]:
        """Return distances as a NumPy 2D array."""
        return np.asarray(self.distances_meters, dtype=np.float64)


class MatrixProvider(Protocol):
    """Protocol for origin-destination matrix providers.

    `origin_departure_times` carries one estimated departure time per origin, so
    a time-aware provider can band each leg by when its origin is actually left.
    Providers that model no time-of-day variation ignore it. Computing those
    times needs service times and work rules — domain knowledge that lives in
    `analysis`, not here — so callers supply the vector rather than the provider
    deriving it.
    """

    name: str

    @property
    def is_time_aware(self) -> bool:
        """Whether this provider's result depends on WHEN the trip is driven.

        A live-traffic engine (Google) and the time-banding wrapper do; OSRM's
        free-flow times and the haversine estimate do not. Callers use this to
        decide whether to pass the plan's departure time at all: handing a
        departure time to a time-agnostic provider is not wrong, but it
        fragments a wrapping cache by a value that never changes the answer. So
        the call sites pass it only when the provider will actually use it.

        Declared as a read-only property so both plain class attributes (the
        leaf providers) and delegating properties (the cache/fallback wrappers)
        satisfy it.
        """
        ...

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult: ...
