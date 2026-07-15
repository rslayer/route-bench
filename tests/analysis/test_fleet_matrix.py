"""Tests for the combined fleet matrix and the wired fleet benchmark.

The fleet benchmark previously never ran: nothing built the combined matrix, so
FleetBenchmarkTool always returned [] and AnalysisReport.benchmark was always
None. These tests pin the wiring shut.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from unittest.mock import MagicMock

import pytest

from routebench.agent.client import LLMClient, LLMResponse
from routebench.agent.orchestrator import AnalysisOrchestrator
from routebench.analysis.benchmark.fleet_matrix import (
    combined_departure_schedule,
    fleet_coords,
    fleet_depot,
    get_fleet_matrix,
)
from routebench.core.config import URBAN_US_PROFILE, AnalysisConfig, WorkRules
from routebench.core.exceptions import InvalidInputError
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.traffic import TrafficAdjustedProvider

LEG_SECONDS = 600.0
LEG_METERS = 5000.0


class FlatProvider:
    """Uniform free-flow matrix; counts fetches."""

    name: str = "flat"

    def __init__(self) -> None:
        self.call_count = 0
        self.requested_sizes: list[int] = []

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        self.call_count += 1
        self.requested_sizes.append(len(origins))
        n = len(origins)
        return MatrixResult(
            durations_seconds=[[LEG_SECONDS] * n for _ in range(n)],
            distances_meters=[[LEG_METERS] * n for _ in range(n)],
            provider="flat",
            cached=False,
        )


def _route(
    route_id: str,
    n_stops: int = 2,
    depot_lat: float = 32.79,
    depot_lon: float = -96.80,
    start_hour: int = 7,
    start_minute: int = 30,
) -> Route:
    stops = [
        Stop(
            route_id=route_id,
            stop_sequence=i,
            latitude=32.80 + 0.01 * i,
            longitude=-96.80,
            service_time_minutes=5.0,
        )
        for i in range(1, n_stops + 1)
    ]
    return Route(
        route_id=route_id,
        stops=stops,
        depot_lat=depot_lat,
        depot_lon=depot_lon,
        planned_start_time=datetime(2025, 1, 15, start_hour, start_minute, tzinfo=UTC),
    )


def _fleet(*routes: Route) -> Fleet:
    return Fleet(routes=list(routes), upload_id="t", uploaded_at=datetime(2025, 1, 15, tzinfo=UTC))


class TestFleetDepot:
    """solve_vrptw models one depot node for all vehicles."""

    def test_shared_depot_detected(self) -> None:
        fleet = _fleet(_route("R1"), _route("R2"))
        assert fleet_depot(fleet) == (32.79, -96.80)

    def test_differing_depots_rejected(self) -> None:
        fleet = _fleet(_route("R1", depot_lat=32.79), _route("R2", depot_lat=33.90))
        assert fleet_depot(fleet) is None

    def test_float_noise_tolerated(self) -> None:
        fleet = _fleet(_route("R1", depot_lat=32.79), _route("R2", depot_lat=32.79 + 1e-9))
        assert fleet_depot(fleet) is not None

    def test_empty_fleet(self) -> None:
        assert fleet_depot(_fleet()) is None


class TestFleetCoords:
    """Depot at index 0, every stop at 1..N in fleet order."""

    def test_layout(self) -> None:
        fleet = _fleet(_route("R1", n_stops=2), _route("R2", n_stops=3))
        coords = fleet_coords(fleet)
        assert len(coords) == 1 + fleet.total_stops() == 6
        assert coords[0] == (32.79, -96.80)

    def test_rejects_split_depots(self) -> None:
        fleet = _fleet(_route("R1", depot_lat=32.79), _route("R2", depot_lat=33.90))
        with pytest.raises(InvalidInputError, match="do not share a single depot"):
            fleet_coords(fleet)


class TestCombinedDepartureSchedule:
    """One departure per combined-matrix index."""

    def test_length_matches_coords(self) -> None:
        fleet = _fleet(_route("R1", n_stops=2), _route("R2", n_stops=3))
        provider = TrafficAdjustedProvider(FlatProvider(), URBAN_US_PROFILE)
        schedule = combined_departure_schedule(fleet, provider, WorkRules())
        assert len(schedule) == len(fleet_coords(fleet))

    def test_depot_uses_median_start(self) -> None:
        """Three routes, three start times -> the middle one bands the depot row."""
        fleet = _fleet(
            _route("R1", start_hour=6),
            _route("R2", start_hour=8),
            _route("R3", start_hour=10),
        )
        provider = TrafficAdjustedProvider(FlatProvider(), URBAN_US_PROFILE)
        schedule = combined_departure_schedule(fleet, provider, WorkRules())
        # 08:30 start + 15min pre-trip
        assert schedule[0].time() == time(8, 45)

    def test_stops_band_on_their_own_route_schedule(self) -> None:
        """A late route's stops must not inherit an early route's clock."""
        fleet = _fleet(
            _route("R1", n_stops=1, start_hour=7), _route("R2", n_stops=1, start_hour=13)
        )
        provider = TrafficAdjustedProvider(FlatProvider(), URBAN_US_PROFILE)
        schedule = combined_departure_schedule(fleet, provider, WorkRules())
        # index 1 = R1's only stop (morning), index 2 = R2's only stop (afternoon)
        assert schedule[1].hour < 12
        assert schedule[2].hour >= 12


class TestGetFleetMatrix:
    """Two-pass shape, mirroring get_route_matrix."""

    def test_free_flow_without_profile(self) -> None:
        fleet = _fleet(_route("R1"), _route("R2"))
        matrix = get_fleet_matrix(fleet, FlatProvider(), WorkRules())  # type: ignore[arg-type]
        assert matrix.durations_seconds[0][0] == pytest.approx(LEG_SECONDS)

    def test_bands_when_profile_active(self) -> None:
        fleet = _fleet(_route("R1", start_hour=7), _route("R2", start_hour=7))
        provider = TrafficAdjustedProvider(FlatProvider(), URBAN_US_PROFILE)
        matrix = get_fleet_matrix(fleet, provider, WorkRules())
        # 07:xx departures sit in the 0.75x band -> 600 / 0.75 = 800s
        assert matrix.durations_seconds[0][0] == pytest.approx(800.0)

    def test_distances_untouched_by_banding(self) -> None:
        fleet = _fleet(_route("R1", start_hour=7), _route("R2", start_hour=7))
        provider = TrafficAdjustedProvider(FlatProvider(), URBAN_US_PROFILE)
        matrix = get_fleet_matrix(fleet, provider, WorkRules())
        assert matrix.distances_meters[0][0] == pytest.approx(LEG_METERS)

    def test_matrix_is_square_and_sized_to_the_fleet(self) -> None:
        fleet = _fleet(_route("R1", n_stops=2), _route("R2", n_stops=3))
        matrix = get_fleet_matrix(fleet, FlatProvider(), WorkRules())  # type: ignore[arg-type]
        n = 1 + fleet.total_stops()
        assert len(matrix.durations_seconds) == n
        assert all(len(row) == n for row in matrix.durations_seconds)

    def test_without_work_rules_stays_free_flow(self) -> None:
        fleet = _fleet(_route("R1", start_hour=7), _route("R2", start_hour=7))
        provider = TrafficAdjustedProvider(FlatProvider(), URBAN_US_PROFILE)
        matrix = get_fleet_matrix(fleet, provider, work_rules=None)
        assert matrix.durations_seconds[0][0] == pytest.approx(LEG_SECONDS)


def _fast_config(**kwargs: Any) -> AnalysisConfig:
    """OR-Tools spends its whole time limit, so keep tests to ~1s of solving."""
    return AnalysisConfig(
        route_benchmark_time_limit_s=1,
        fleet_benchmark_time_limit_s=1,
        **kwargs,
    )


def _llm_calling(*tool_names: str) -> MagicMock:
    """An LLM that calls the named tools, then stops."""
    client = MagicMock(spec=LLMClient)
    client._model = "test-model"

    def _resp(content: list[dict[str, Any]]) -> LLMResponse:
        return LLMResponse(
            content=content, stop_reason="end_turn", input_tokens=1, output_tokens=1, model="m"
        )

    client.generate.side_effect = [
        _resp([{"type": "tool_use", "id": f"c{i}", "name": name, "input": {}}])
        for i, name in enumerate(tool_names)
    ] + [
        _resp([{"type": "tool_use", "id": "done", "name": "analysis_complete", "input": {}}]),
    ]
    return client


class TestBenchmarkReachesTheReport:
    """The wiring: tools solve, and the structured result lands on the report."""

    def test_fleet_benchmark_populates_report(self) -> None:
        fleet = _fleet(_route("R1", n_stops=3), _route("R2", n_stops=3))
        orch = AnalysisOrchestrator(
            client=_llm_calling("route_benchmark", "fleet_benchmark"),
            config=_fast_config(),
            matrix_provider=FlatProvider(),  # type: ignore[arg-type]
        )
        report = orch.run(fleet)

        assert report.benchmark is not None, "benchmark was hardcoded None before this wiring"
        assert set(report.benchmark.per_route) == {"R1", "R2"}
        assert report.benchmark.fleet_level is not None

    def test_fleet_level_actual_distance_is_not_zero(self) -> None:
        """per_route_matrices was never passed, silently zeroing the actual total."""
        fleet = _fleet(_route("R1", n_stops=3), _route("R2", n_stops=3))
        orch = AnalysisOrchestrator(
            client=_llm_calling("fleet_benchmark"),
            config=_fast_config(),
            matrix_provider=FlatProvider(),  # type: ignore[arg-type]
        )
        report = orch.run(fleet)
        assert report.benchmark is not None
        assert report.benchmark.fleet_level is not None
        assert report.benchmark.fleet_level.actual_total_distance > 0

    def test_route_benchmark_alone_yields_no_fleet_level(self) -> None:
        fleet = _fleet(_route("R1", n_stops=3), _route("R2", n_stops=3))
        orch = AnalysisOrchestrator(
            client=_llm_calling("route_benchmark"),
            config=_fast_config(),
            matrix_provider=FlatProvider(),  # type: ignore[arg-type]
        )
        report = orch.run(fleet)
        assert report.benchmark is not None
        assert report.benchmark.per_route
        assert report.benchmark.fleet_level is None

    def test_no_benchmark_tools_leaves_benchmark_none(self) -> None:
        fleet = _fleet(_route("R1", n_stops=3), _route("R2", n_stops=3))
        orch = AnalysisOrchestrator(
            client=_llm_calling("analyze_sequencing"),
            config=_fast_config(),
            matrix_provider=FlatProvider(),  # type: ignore[arg-type]
        )
        assert orch.run(fleet).benchmark is None

    def test_include_benchmark_false_skips_benchmark_tools(self) -> None:
        fleet = _fleet(_route("R1", n_stops=3), _route("R2", n_stops=3))
        orch = AnalysisOrchestrator(
            client=_llm_calling("analyze_sequencing"),
            config=_fast_config(include_benchmark=False),
            matrix_provider=FlatProvider(),  # type: ignore[arg-type]
        )
        report = orch.run(fleet)
        skipped = dict(report.analyses_skipped)
        assert "route_benchmark" in skipped
        assert "fleet_benchmark" in skipped
        assert "include_benchmark" in skipped["route_benchmark"]

    def test_fleet_matrix_not_built_for_non_benchmark_tools(self) -> None:
        """It spans every stop in the fleet; a sequencing run must not pay for it."""
        fleet = _fleet(_route("R1", n_stops=3), _route("R2", n_stops=3))
        provider = FlatProvider()
        orch = AnalysisOrchestrator(
            client=_llm_calling("analyze_sequencing"),
            config=_fast_config(),
            matrix_provider=provider,  # type: ignore[arg-type]
        )
        orch.run(fleet)

        fleet_sized = 1 + fleet.total_stops()
        assert fleet_sized not in provider.requested_sizes, (
            f"combined {fleet_sized}-node matrix was fetched for a non-benchmark tool; "
            f"requested sizes were {provider.requested_sizes}"
        )

    def test_fleet_matrix_built_once_across_repeated_calls(self) -> None:
        """Cached on the orchestrator so two benchmark calls share one fetch."""
        fleet = _fleet(_route("R1", n_stops=3), _route("R2", n_stops=3))
        provider = FlatProvider()
        orch = AnalysisOrchestrator(
            client=_llm_calling("fleet_benchmark", "fleet_benchmark"),
            config=_fast_config(),
            matrix_provider=provider,  # type: ignore[arg-type]
        )
        orch.run(fleet)

        fleet_sized = 1 + fleet.total_stops()
        assert provider.requested_sizes.count(fleet_sized) == 1
