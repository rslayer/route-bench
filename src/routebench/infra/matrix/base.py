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

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult: ...
