"""Tests for analysis/diagnosis tools.

Each tool tested with a fleet where the issue is present and one where it is absent.
"""

from __future__ import annotations

from datetime import UTC, datetime

from routebench.analysis.diagnosis.dispatch import DispatchAnalysis
from routebench.analysis.diagnosis.outliers import OutlierAnalysis
from routebench.analysis.diagnosis.sequencing import SequencingAnalysis
from routebench.analysis.diagnosis.territory import TerritoryAnalysis
from routebench.analysis.diagnosis.time_pressure import TimePressureAnalysis
from routebench.analysis.tools import TOOLS
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult


def _ts(hour: int = 8, minute: int = 0) -> datetime:
    return datetime(2025, 1, 15, hour, minute, 0, tzinfo=UTC)


def _make_stop(route_id: str, seq: int, lat: float, lon: float, svc: float = 5.0) -> Stop:
    return Stop(
        route_id=route_id,
        stop_sequence=seq,
        latitude=lat,
        longitude=lon,
        service_time_minutes=svc,
    )


def _make_route(
    route_id: str,
    stops: list[Stop],
    depot_lat: float = 32.825,
    depot_lon: float = -96.775,
    start_time: datetime | None = None,
) -> Route:
    return Route(
        route_id=route_id,
        stops=stops,
        depot_lat=depot_lat,
        depot_lon=depot_lon,
        planned_start_time=start_time or _ts(),
    )


def _make_fleet(*routes: Route) -> Fleet:
    return Fleet(
        routes=list(routes),
        upload_id="test",
        uploaded_at=_ts(),
    )


def _uniform_matrix(n: int, dist: float = 1000.0, dur: float = 60.0) -> MatrixResult:
    return MatrixResult(
        durations_seconds=[[dur] * n for _ in range(n)],
        distances_meters=[[dist] * n for _ in range(n)],
        provider="mock",
        cached=False,
    )


class TestToolsRegistry:
    """Verify the TOOLS registry is populated correctly."""

    def test_all_tools_registered(self) -> None:
        # Force import to trigger registration
        import routebench.analysis.diagnosis  # noqa: F401

        expected = {
            "analyze_sequencing",
            "analyze_time_pressure",
            "analyze_outliers",
            "analyze_territory",
            "analyze_dispatch",
            "analyze_compliance",
            "analyze_reachability",
            "analyze_service_sanity",
            "route_benchmark",
            "fleet_benchmark",
        }
        assert set(TOOLS.keys()) == expected


class TestSequencingAnalysis:
    """Tests for sequencing diagnosis."""

    def test_always_applicable(self) -> None:
        tool = SequencingAnalysis()
        fleet = _make_fleet(_make_route("R1", [_make_stop("R1", 1, 32.83, -96.77)]))
        assert tool.applicability_check(fleet).is_applicable

    def test_flags_zigzag_route(self) -> None:
        """A zigzag route (crossing) should be flagged."""
        tool = SequencingAnalysis(threshold=1.10)

        stops = [
            _make_stop("R1", 1, 32.83, -96.77),  # NE
            _make_stop("R1", 2, 32.81, -96.79),  # SW
            _make_stop("R1", 3, 32.83, -96.79),  # NW
            _make_stop("R1", 4, 32.81, -96.77),  # SE
        ]
        route = _make_route("R1", stops)
        fleet = _make_fleet(route)

        # Build a matrix where the zigzag is clearly suboptimal
        # Index: 0=depot, 1=NE, 2=SW, 3=NW, 4=SE
        n = 5
        # Create distance matrix where adjacent stops in sequence are far
        # but geographically close stops are near
        dists = [[0.0] * n for _ in range(n)]
        # Depot at center
        for i in range(1, n):
            dists[0][i] = 2000.0
            dists[i][0] = 2000.0
        # NE(1) close to NW(3), SW(2) close to SE(4)
        dists[1][3] = 500.0
        dists[3][1] = 500.0
        dists[2][4] = 500.0
        dists[4][2] = 500.0
        # NE(1) far from SW(2), NW(3) far from SE(4) — the zigzag legs
        dists[1][2] = 5000.0
        dists[2][1] = 5000.0
        dists[3][4] = 5000.0
        dists[4][3] = 5000.0
        # Other pairs
        dists[1][4] = 3000.0
        dists[4][1] = 3000.0
        dists[2][3] = 3000.0
        dists[3][2] = 3000.0

        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )

        findings = tool.run(fleet, matrices={"R1": matrix})
        assert len(findings) >= 1
        assert findings[0].category == "sequencing"
        assert "crossing" in findings[0].hypothesis.lower()

    def test_no_flag_for_good_route(self) -> None:
        """A well-sequenced route should not be flagged."""
        tool = SequencingAnalysis(threshold=1.30)

        # Linear route: depot → s1 → s2 → s3 → depot
        stops = [
            _make_stop("R1", 1, 32.83, -96.78),
            _make_stop("R1", 2, 32.84, -96.78),
            _make_stop("R1", 3, 32.85, -96.78),
        ]
        route = _make_route("R1", stops)
        fleet = _make_fleet(route)

        # Uniform matrix → NN heuristic produces same or similar distance
        matrix = _uniform_matrix(4, dist=1000.0)
        findings = tool.run(fleet, matrices={"R1": matrix})
        assert len(findings) == 0


class TestTimePressureAnalysis:
    """Tests for time pressure diagnosis."""

    def test_flags_high_idle(self) -> None:
        """Route with significant idle should be flagged."""
        tool = TimePressureAnalysis()
        stops = [_make_stop("R1", i, 32.83 + i * 0.01, -96.77) for i in range(1, 4)]
        route = _make_route("R1", stops)
        fleet = _make_fleet(route)
        matrix = _uniform_matrix(4)

        # Pre-compute metrics with high idle via kwargs
        findings = tool.run(
            fleet,
            matrices={"R1": matrix},
            route_time_metrics={
                "R1": {
                    "idle_time_hours": 1.5,
                    "total_time_hours": 8.0,
                }
            },
        )
        assert len(findings) == 1
        assert findings[0].category == "time_pressure"

    def test_no_flag_low_idle(self) -> None:
        """Route with low idle should not be flagged."""
        tool = TimePressureAnalysis()
        stops = [_make_stop("R1", i, 32.83 + i * 0.01, -96.77) for i in range(1, 4)]
        route = _make_route("R1", stops)
        fleet = _make_fleet(route)
        matrix = _uniform_matrix(4)

        findings = tool.run(
            fleet,
            matrices={"R1": matrix},
            route_time_metrics={
                "R1": {
                    "idle_time_hours": 0.1,
                    "total_time_hours": 8.0,
                }
            },
        )
        assert len(findings) == 0


class TestOutlierAnalysis:
    """Tests for outlier diagnosis."""

    def test_not_applicable_few_stops(self) -> None:
        """Routes with <5 stops → not applicable."""
        tool = OutlierAnalysis()
        stops = [_make_stop("R1", i, 32.83 + i * 0.01, -96.77) for i in range(1, 4)]
        fleet = _make_fleet(_make_route("R1", stops))
        assert not tool.applicability_check(fleet).is_applicable

    def test_flags_outlier_stop(self) -> None:
        """A stop far from all others should be flagged."""
        tool = OutlierAnalysis()

        # 5 stops: 4 close together, 1 far away
        stops = [
            _make_stop("R1", 1, 32.83, -96.77),
            _make_stop("R1", 2, 32.831, -96.771),
            _make_stop("R1", 3, 32.832, -96.772),
            _make_stop("R1", 4, 32.833, -96.773),
            _make_stop("R1", 5, 33.00, -96.50),  # outlier
        ]
        route = _make_route("R1", stops)
        fleet = _make_fleet(route)

        # Matrix: stops 1-4 are close (100m), stop 5 is far (50000m)
        n = 6  # depot + 5 stops
        dists = [[100.0] * n for _ in range(n)]
        for i in range(n):
            dists[i][i] = 0.0
        # Make stop 5 (index 5) far from everything
        for i in range(n):
            if i != 5:
                dists[i][5] = 50000.0
                dists[5][i] = 50000.0

        matrix = MatrixResult(
            durations_seconds=dists,
            distances_meters=dists,
            provider="mock",
            cached=False,
        )

        findings = tool.run(fleet, matrices={"R1": matrix})
        assert len(findings) >= 1
        assert findings[0].category == "outlier"
        assert any((route.route_id, 5) in f.references.stop_sequences for f in findings)

    def test_no_outlier_in_uniform(self) -> None:
        """Uniformly spaced stops should not flag outliers."""
        tool = OutlierAnalysis()
        stops = [_make_stop("R1", i, 32.83 + i * 0.01, -96.77) for i in range(1, 6)]
        route = _make_route("R1", stops)
        fleet = _make_fleet(route)
        matrix = _uniform_matrix(6, dist=1000.0)
        findings = tool.run(fleet, matrices={"R1": matrix})
        assert len(findings) == 0


class TestTerritoryAnalysis:
    """Tests for territory diagnosis."""

    def test_not_applicable_single_route(self) -> None:
        tool = TerritoryAnalysis()
        stops = [_make_stop("R1", 1, 32.83, -96.77)]
        fleet = _make_fleet(_make_route("R1", stops))
        assert not tool.applicability_check(fleet).is_applicable

    def test_flags_overlap(self) -> None:
        """Two routes with interleaving stops should flag overlap."""
        tool = TerritoryAnalysis()

        # Route A: stops in a cluster around (32.83, -96.77)
        stops_a = [
            _make_stop("RA", 1, 32.830, -96.770),
            _make_stop("RA", 2, 32.831, -96.771),
            _make_stop("RA", 3, 32.832, -96.772),
            _make_stop("RA", 4, 32.833, -96.773),
        ]
        # Route B: stops overlapping with A's territory
        stops_b = [
            _make_stop("RB", 1, 32.8305, -96.7705),
            _make_stop("RB", 2, 32.8315, -96.7715),
            _make_stop("RB", 3, 32.8325, -96.7725),
            _make_stop("RB", 4, 32.8335, -96.7735),
        ]

        fleet = _make_fleet(
            _make_route("RA", stops_a),
            _make_route("RB", stops_b),
        )

        findings = tool.run(fleet)
        overlap_findings = [f for f in findings if "overlap" in f.title.lower()]
        assert len(overlap_findings) >= 1

    def test_no_overlap_distant_routes(self) -> None:
        """Well-separated routes should not flag overlap."""
        tool = TerritoryAnalysis()

        stops_a = [
            _make_stop("RA", 1, 32.83, -96.77),
            _make_stop("RA", 2, 32.831, -96.771),
            _make_stop("RA", 3, 32.832, -96.772),
        ]
        # Route B is far away
        stops_b = [
            _make_stop("RB", 1, 33.50, -97.50),
            _make_stop("RB", 2, 33.51, -97.51),
            _make_stop("RB", 3, 33.52, -97.52),
        ]

        fleet = _make_fleet(
            _make_route("RA", stops_a),
            _make_route("RB", stops_b),
        )

        findings = tool.run(fleet)
        overlap_findings = [f for f in findings if "overlap" in f.title.lower()]
        assert len(overlap_findings) == 0

    def test_flags_depot_stress(self) -> None:
        """Routes far from depot should flag depot stress."""
        tool = TerritoryAnalysis(depot_stress_miles=5.0)

        # Depot at origin, stops far away (~50 miles north)
        stops_a = [
            _make_stop("RA", 1, 33.5, -96.77),
            _make_stop("RA", 2, 33.51, -96.77),
            _make_stop("RA", 3, 33.52, -96.77),
        ]
        stops_b = [
            _make_stop("RB", 1, 33.6, -96.77),
            _make_stop("RB", 2, 33.61, -96.77),
            _make_stop("RB", 3, 33.62, -96.77),
        ]

        fleet = _make_fleet(
            _make_route("RA", stops_a),
            _make_route("RB", stops_b),
        )

        findings = tool.run(fleet)
        depot_findings = [f for f in findings if "depot stress" in f.title.lower()]
        assert len(depot_findings) == 1


class TestDispatchAnalysis:
    """Tests for dispatch diagnosis."""

    def test_not_applicable_single_route(self) -> None:
        tool = DispatchAnalysis()
        stops = [_make_stop("R1", 1, 32.83, -96.77)]
        fleet = _make_fleet(_make_route("R1", stops))
        assert not tool.applicability_check(fleet).is_applicable

    def test_flags_clustered_dispatch(self) -> None:
        """All routes starting at same time should flag."""
        tool = DispatchAnalysis()

        routes = []
        for i in range(5):
            stops = [_make_stop(f"R{i}", 1, 32.83 + i * 0.01, -96.77)]
            routes.append(_make_route(f"R{i}", stops, start_time=_ts(8, 0)))

        fleet = _make_fleet(*routes)
        findings = tool.run(fleet)
        assert len(findings) == 1
        assert findings[0].category == "dispatch"

    def test_no_flag_staggered_dispatch(self) -> None:
        """Routes with staggered start times should not flag."""
        tool = DispatchAnalysis()

        routes = []
        for i in range(5):
            stops = [_make_stop(f"R{i}", 1, 32.83 + i * 0.01, -96.77)]
            routes.append(_make_route(f"R{i}", stops, start_time=_ts(6 + i, 0)))

        fleet = _make_fleet(*routes)
        findings = tool.run(fleet)
        assert len(findings) == 0
