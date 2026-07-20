"""A time-aware engine is handed the plan's departure time; a free-flow one is not.

The point of the capability flag: a live-traffic engine (Google) should grade
against the traffic the plan would actually face — its `planned_start_time` —
not "now". A free-flow engine (OSRM) must NOT be handed a departure time,
because a wrapping cache keys on it and would fragment by a value that never
changes the free-flow answer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from routebench.analysis.benchmark.fleet_matrix import get_fleet_matrix
from routebench.analysis.scoring.distance import get_route_matrix
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult


def _route(route_id: str, start: datetime) -> Route:
    return Route(
        route_id=route_id,
        stops=[
            Stop(
                route_id=route_id,
                stop_sequence=i,
                latitude=32.80 + 0.01 * i,
                longitude=-96.80,
                service_time_minutes=5.0,
            )
            for i in range(1, 4)
        ],
        depot_lat=32.79,
        depot_lon=-96.80,
        planned_start_time=start,
    )


class _RecordingProvider:
    """Records the departure_time of every get_matrix call."""

    def __init__(self, *, time_aware: bool) -> None:
        self.is_time_aware = time_aware
        self.name = "recording"
        self.seen_departures: list[datetime | None] = []

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        self.seen_departures.append(departure_time)
        n = len(origins)
        return MatrixResult(
            durations_seconds=[[100.0] * n for _ in range(n)],
            distances_meters=[[1000.0] * n for _ in range(n)],
            provider="recording",
            cached=False,
        )


class TestCapabilityPropagatesThroughWrappers:
    """The call sites see the wrapped chain, not the leaf engine, so the flag
    has to survive Cached(...) and Fallback(...). This is what makes a Google
    engine behind the production chain actually get the departure time."""

    def test_fallback_and_cache_follow_the_primary(self, tmp_path: object) -> None:
        from routebench.infra.matrix.cache import CachedMatrixProvider
        from routebench.infra.matrix.fallback import FallbackMatrixProvider
        from routebench.infra.matrix.haversine import HaversineMatrixProvider

        time_aware = _RecordingProvider(time_aware=True)
        free_flow = _RecordingProvider(time_aware=False)

        # The production shape: Fallback(Cached(engine), Haversine).
        aware_chain = FallbackMatrixProvider(
            primary=CachedMatrixProvider(backend=time_aware, cache_dir=tmp_path),  # type: ignore[arg-type]
            fallback=HaversineMatrixProvider(),
        )
        free_chain = FallbackMatrixProvider(
            primary=CachedMatrixProvider(backend=free_flow, cache_dir=tmp_path),  # type: ignore[arg-type]
            fallback=HaversineMatrixProvider(),
        )
        assert aware_chain.is_time_aware is True
        assert free_chain.is_time_aware is False


class TestPerRouteMatrix:
    def test_time_aware_engine_gets_the_plan_start_time(self) -> None:
        start = datetime(2025, 3, 4, 8, 30, tzinfo=UTC)
        provider = _RecordingProvider(time_aware=True)
        get_route_matrix(_route("R1", start), provider)  # type: ignore[arg-type]
        assert provider.seen_departures == [start]

    def test_free_flow_engine_is_not_handed_a_departure_time(self) -> None:
        start = datetime(2025, 3, 4, 8, 30, tzinfo=UTC)
        provider = _RecordingProvider(time_aware=False)
        get_route_matrix(_route("R1", start), provider)  # type: ignore[arg-type]
        assert provider.seen_departures == [None]


class TestFleetMatrix:
    def _fleet(self, starts: list[datetime]) -> Fleet:
        return Fleet(
            routes=[_route(f"R{i}", s) for i, s in enumerate(starts)],
            upload_id="t",
            uploaded_at=datetime(2025, 3, 4, tzinfo=UTC),
        )

    def test_time_aware_engine_gets_the_median_start_time(self) -> None:
        starts = [
            datetime(2025, 3, 4, 7, 0, tzinfo=UTC),
            datetime(2025, 3, 4, 8, 0, tzinfo=UTC),  # lower median of 3
            datetime(2025, 3, 4, 9, 0, tzinfo=UTC),
        ]
        provider = _RecordingProvider(time_aware=True)
        get_fleet_matrix(self._fleet(starts), provider)  # type: ignore[arg-type]
        assert provider.seen_departures[0] == starts[1]

    def test_free_flow_engine_is_not_handed_a_departure_time(self) -> None:
        starts = [
            datetime(2025, 3, 4, 7, 0, tzinfo=UTC),
            datetime(2025, 3, 4, 8, 0, tzinfo=UTC),
        ]
        provider = _RecordingProvider(time_aware=False)
        get_fleet_matrix(self._fleet(starts), provider)  # type: ignore[arg-type]
        assert provider.seen_departures == [None]
