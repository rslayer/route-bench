"""Tests for analysis/scoring — descriptive scorecard metrics.

Uses hand-constructed routes with mock matrix providers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from routebench.analysis.scoring import compute_scorecard
from routebench.analysis.scoring.compliance import compute_compliance_metrics
from routebench.analysis.scoring.density import compute_density_metrics
from routebench.analysis.scoring.distance import compute_distance_metrics
from routebench.analysis.scoring.time import compute_time_metrics
from routebench.analysis.scoring.utilization import compute_utilization_metrics
from routebench.core.config import AnalysisConfig, WorkRules
from routebench.core.findings import FleetMetrics, RouteMetrics
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult


def _mock_matrix(n: int, distance_m: float = 5000.0, duration_s: float = 300.0) -> MatrixResult:
    """Create a uniform mock matrix of size n x n."""
    return MatrixResult(
        durations_seconds=[[duration_s] * n for _ in range(n)],
        distances_meters=[[distance_m] * n for _ in range(n)],
        provider="mock",
        cached=False,
    )


def _make_route(
    route_id: str = "R001",
    n_stops: int = 3,
    depot_lat: float = 32.825,
    depot_lon: float = -96.775,
    service_minutes: float = 5.0,
    capacity_units: float | None = None,
    demand_per_stop: float | None = None,
) -> Route:
    """Build a test route."""
    stops = []
    for i in range(1, n_stops + 1):
        stops.append(
            Stop(
                route_id=route_id,
                stop_sequence=i,
                latitude=depot_lat + 0.01 * i,
                longitude=depot_lon + 0.01 * i,
                stop_type="delivery",
                service_time_minutes=service_minutes,
                demand_units=demand_per_stop,
            )
        )
    return Route(
        route_id=route_id,
        stops=stops,
        depot_lat=depot_lat,
        depot_lon=depot_lon,
        planned_start_time=datetime(2025, 1, 15, 8, 0, 0, tzinfo=timezone.utc),
        vehicle_capacity_units=capacity_units,
    )


class TestDistanceMetrics:
    """Tests for distance scoring."""

    def test_3_stop_route_distance(self) -> None:
        """3-stop route with 5km legs: depot→s1→s2→s3→depot = 4 legs * 5km."""
        route = _make_route(n_stops=3)
        # 4x4 matrix (depot + 3 stops)
        matrix = _mock_matrix(4, distance_m=5000.0)
        result = compute_distance_metrics(route, matrix)

        expected_miles = 4 * (5000.0 / 1609.34)
        assert result["total_distance_miles"] == pytest.approx(expected_miles, rel=0.01)
        assert len(result["leg_distances_miles"]) == 4  # type: ignore[arg-type]

    def test_1_stop_route(self) -> None:
        """Single stop: depot→s1→depot = 2 legs."""
        route = _make_route(n_stops=1)
        matrix = _mock_matrix(2, distance_m=8000.0)
        result = compute_distance_metrics(route, matrix)

        expected = 2 * (8000.0 / 1609.34)
        assert result["total_distance_miles"] == pytest.approx(expected, rel=0.01)
        assert len(result["leg_distances_miles"]) == 2  # type: ignore[arg-type]

    def test_0_stop_route(self) -> None:
        """Empty route: no distance."""
        route = _make_route(n_stops=0)
        matrix = _mock_matrix(1)
        result = compute_distance_metrics(route, matrix)
        assert result["total_distance_miles"] == 0.0


class TestTimeMetrics:
    """Tests for time scoring."""

    def test_3_stop_time(self) -> None:
        """3-stop route with known durations."""
        route = _make_route(n_stops=3, service_minutes=5.0)
        # 4 legs * 300s = 1200s drive, 3 * 300s = 900s service
        matrix = _mock_matrix(4, duration_s=300.0)
        work_rules = WorkRules()
        result = compute_time_metrics(route, matrix, work_rules)

        drive_hours = 4 * 300.0 / 3600.0
        service_hours = 3 * 5.0 / 60.0
        assert result["drive_time_hours"] == pytest.approx(drive_hours, rel=0.01)
        assert result["service_time_hours"] == pytest.approx(service_hours, rel=0.01)

    def test_total_time_includes_pre_post_trip(self) -> None:
        """Total time includes pre-trip and post-trip."""
        route = _make_route(n_stops=1, service_minutes=0.0)
        matrix = _mock_matrix(2, duration_s=0.0)
        work_rules = WorkRules(pre_trip_minutes=15.0, post_trip_minutes=15.0)
        result = compute_time_metrics(route, matrix, work_rules)

        # Should include 30 min pre+post = 0.5 hours
        assert float(result["total_time_hours"]) >= 0.5

    def test_1_stop_no_crash(self) -> None:
        """Single stop route doesn't crash."""
        route = _make_route(n_stops=1)
        matrix = _mock_matrix(2, duration_s=100.0)
        result = compute_time_metrics(route, matrix, WorkRules())
        assert float(result["total_time_hours"]) > 0

    def test_lunch_inserted_after_threshold(self) -> None:
        """Lunch is inserted when shift exceeds lunch_after_hours."""
        route = _make_route(n_stops=10, service_minutes=30.0)
        # 11 legs * 1800s = 5.5 hours drive, 10 * 30min = 5 hours service
        # Total > 6 hours → lunch should be inserted
        matrix = _mock_matrix(11, duration_s=1800.0)
        work_rules = WorkRules(lunch_after_hours=6.0, lunch_minutes=30.0)
        result = compute_time_metrics(route, matrix, work_rules)

        # Idle time should include 30 min lunch
        assert float(result["idle_time_hours"]) >= 0.5


class TestDensityMetrics:
    """Tests for density scoring."""

    def test_stops_per_hour(self) -> None:
        """Stops per hour = n_stops / total_time."""
        dist = {"total_distance_miles": 10.0, "avg_inter_stop_distance_miles": 2.0}
        time = {"total_time_hours": 2.0}
        route = _make_route(n_stops=5)
        result = compute_density_metrics(route, dist, time)
        assert result["stops_per_hour"] == pytest.approx(2.5)

    def test_convex_hull_area_positive(self) -> None:
        """A route with spread-out stops has positive hull area."""
        route = _make_route(n_stops=5)
        dist = {"total_distance_miles": 10.0, "avg_inter_stop_distance_miles": 2.0}
        time = {"total_time_hours": 2.0}
        result = compute_density_metrics(route, dist, time)
        assert float(result["convex_hull_area_sq_miles"]) > 0

    def test_2_stops_zero_hull(self) -> None:
        """Two collinear stops produce zero hull area."""
        route = _make_route(n_stops=2)
        dist = {"total_distance_miles": 10.0, "avg_inter_stop_distance_miles": 5.0}
        time = {"total_time_hours": 1.0}
        result = compute_density_metrics(route, dist, time)
        assert float(result["convex_hull_area_sq_miles"]) == 0.0


class TestUtilizationMetrics:
    """Tests for utilization scoring."""

    def test_with_capacity_and_demand(self) -> None:
        """Utilization = total demand / capacity."""
        route = _make_route(n_stops=3, capacity_units=100.0, demand_per_stop=10.0)
        result = compute_utilization_metrics(route)
        cap_util: dict[str, float] = result["capacity_utilization"]  # type: ignore[assignment]
        assert "units" in cap_util
        assert cap_util["units"] == pytest.approx(0.3)

    def test_no_capacity_no_utilization(self) -> None:
        """No capacity data → no utilization entries."""
        route = _make_route(n_stops=3)
        result = compute_utilization_metrics(route)
        cap_util: dict[str, float] = result["capacity_utilization"]  # type: ignore[assignment]
        assert len(cap_util) == 0


class TestComplianceMetrics:
    """Tests for compliance scoring."""

    def test_no_violations_no_overrun(self) -> None:
        """Clean route with no violations."""
        route = _make_route(n_stops=3)
        time_metrics: dict[str, object] = {
            "shift_overrun_minutes": 0.0,
            "total_time_hours": 4.0,
            "idle_time_hours": 0.0,
        }
        result = compute_compliance_metrics(route, time_metrics, WorkRules())
        assert result["time_window_violations"] == 0
        assert result["shift_overrun_minutes"] == 0.0


class TestComputeScorecard:
    """Tests for the top-level scorecard orchestrator."""

    def test_scorecard_returns_fleet_and_route_metrics(self) -> None:
        """compute_scorecard returns FleetMetrics and per-route RouteMetrics."""

        class MockMatrixProvider:
            name = "mock"

            def get_matrix(
                self,
                origins: list[tuple[float, float]],
                destinations: list[tuple[float, float]],
                departure_time: datetime | None = None,
            ) -> MatrixResult:
                n = len(origins)
                return _mock_matrix(n, distance_m=5000.0, duration_s=300.0)

        route1 = _make_route("R001", n_stops=3)
        route2 = _make_route("R002", n_stops=5)
        fleet = Fleet(
            routes=[route1, route2],
            upload_id="test",
            uploaded_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
        )

        fm, rm = compute_scorecard(fleet, MockMatrixProvider(), AnalysisConfig())  # type: ignore[arg-type]

        assert isinstance(fm, FleetMetrics)
        assert fm.total_routes == 2
        assert fm.total_stops == 8
        assert "R001" in rm
        assert "R002" in rm
        assert isinstance(rm["R001"], RouteMetrics)
        assert rm["R001"].stop_count == 3
        assert rm["R002"].stop_count == 5
        assert fm.total_distance_miles > 0


class TestHypothesis:
    """Property-based tests using Hypothesis."""

    @given(n_stops=st.integers(min_value=1, max_value=20))
    @settings(max_examples=20)
    def test_total_distance_non_negative(self, n_stops: int) -> None:
        """total_distance_miles >= 0 for any non-empty route."""
        route = _make_route(n_stops=n_stops)
        matrix = _mock_matrix(n_stops + 1, distance_m=1000.0)
        result = compute_distance_metrics(route, matrix)
        assert float(result["total_distance_miles"]) >= 0

    @given(n_stops=st.integers(min_value=1, max_value=20))
    @settings(max_examples=20)
    def test_total_time_gte_drive_plus_service(self, n_stops: int) -> None:
        """total_time_hours >= drive_time_hours + service_time_hours."""
        route = _make_route(n_stops=n_stops, service_minutes=5.0)
        matrix = _mock_matrix(n_stops + 1, duration_s=300.0)
        result = compute_time_metrics(route, matrix, WorkRules())
        total = float(result["total_time_hours"])
        drive = float(result["drive_time_hours"])
        service = float(result["service_time_hours"])
        assert total >= drive + service - 0.001  # small float tolerance
