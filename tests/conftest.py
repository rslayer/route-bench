"""Shared test fixtures for RouteBench."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult


def make_ts(hour: int = 8, minute: int = 0) -> datetime:
    """Create a test timestamp."""
    return datetime(2025, 1, 15, hour, minute, 0, tzinfo=UTC)


def make_stop(
    route_id: str,
    seq: int,
    lat: float = 32.83,
    lon: float = -96.77,
    svc: float = 5.0,
    demand_units: float | None = None,
) -> Stop:
    """Create a test stop."""
    return Stop(
        route_id=route_id,
        stop_sequence=seq,
        latitude=lat + seq * 0.01,
        longitude=lon,
        service_time_minutes=svc,
        demand_units=demand_units,
    )


def make_route(
    route_id: str = "R001",
    n_stops: int = 3,
    depot_lat: float = 32.825,
    depot_lon: float = -96.775,
    service_minutes: float = 5.0,
    capacity_units: float | None = None,
    demand_per_stop: float | None = None,
    start_time: datetime | None = None,
    stops: list[Stop] | None = None,
) -> Route:
    """Create a test route."""
    if stops is None:
        stops = [
            make_stop(route_id, i, depot_lat, depot_lon, service_minutes, demand_per_stop)
            for i in range(1, n_stops + 1)
        ]
    return Route(
        route_id=route_id,
        stops=stops,
        depot_lat=depot_lat,
        depot_lon=depot_lon,
        planned_start_time=start_time or make_ts(),
        vehicle_capacity_units=capacity_units,
    )


def make_fleet(*routes: Route, upload_id: str = "test") -> Fleet:
    """Create a test fleet."""
    return Fleet(
        routes=list(routes),
        upload_id=upload_id,
        uploaded_at=make_ts(),
    )


def mock_matrix(
    n: int, distance_m: float = 5000.0, duration_s: float = 300.0
) -> MatrixResult:
    """Create a uniform mock matrix of size n x n."""
    return MatrixResult(
        durations_seconds=[[duration_s] * n for _ in range(n)],
        distances_meters=[[distance_m] * n for _ in range(n)],
        provider="mock",
        cached=False,
    )


def mock_matrix_realistic(size: int, seed: int = 42) -> MatrixResult:
    """Create a mock MatrixResult with realistic random distances/durations."""
    rng = np.random.default_rng(seed)
    distances = rng.uniform(500, 5000, size=(size, size))
    durations = rng.uniform(60, 600, size=(size, size))
    np.fill_diagonal(distances, 0.0)
    np.fill_diagonal(durations, 0.0)
    return MatrixResult(
        durations_seconds=durations.tolist(),
        distances_meters=distances.tolist(),
        provider="mock",
        cached=False,
    )
