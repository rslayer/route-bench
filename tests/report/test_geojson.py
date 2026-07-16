"""Phase 11 backend prerequisite: the routes.geojson map artifact.

The UI renders geography and never computes it, so anything the map needs must
be in this file and must be correct here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time

import pytest

from routebench.core.findings import (
    AnalysisReport,
    BenchmarkResult,
    Finding,
    FindingEvidence,
    FindingReference,
    FleetBenchmark,
    FleetMetrics,
    RouteBenchmark,
    RouteMetrics,
    StopMigration,
)
from routebench.core.schemas import Fleet, Route, Stop
from routebench.report.geojson import build_routes_geojson

AnalysisReport.model_rebuild()


def _stop(route_id: str, seq: int, lat: float, lon: float, **kw: object) -> Stop:
    return Stop(
        route_id=route_id,
        stop_sequence=seq,
        latitude=lat,
        longitude=lon,
        service_time_minutes=5.0,
        **kw,  # type: ignore[arg-type]
    )


def _route(route_id: str = "R1", n: int = 3, depot: tuple[float, float] = (32.79, -96.80)) -> Route:
    return Route(
        route_id=route_id,
        stops=[_stop(route_id, i, 32.80 + 0.01 * i, -96.80) for i in range(1, n + 1)],
        depot_lat=depot[0],
        depot_lon=depot[1],
        planned_start_time=datetime(2025, 1, 15, 8, 0, tzinfo=UTC),
    )


def _metrics(route_id: str = "R1") -> RouteMetrics:
    return RouteMetrics(
        route_id=route_id,
        total_distance_miles=12.5,
        total_time_hours=2.0,
        drive_time_hours=1.5,
        service_time_hours=0.25,
        idle_time_hours=0.25,
        stop_count=3,
        stops_per_hour=1.5,
        sequencing_index=1.42,
    )


def _report(
    routes: list[Route] | None = None,
    findings: list[Finding] | None = None,
    benchmark: BenchmarkResult | None = None,
) -> AnalysisReport:
    routes = routes or [_route()]
    fleet = Fleet(routes=routes, upload_id="t", uploaded_at=datetime(2025, 1, 15, tzinfo=UTC))
    return AnalysisReport(
        fleet=fleet,
        fleet_metrics=FleetMetrics(
            total_routes=len(routes),
            total_stops=sum(len(r.stops) for r in routes),
            total_distance_miles=12.5,
            total_time_hours=2.0,
            routes_over_shift_cap=0,
        ),
        route_metrics={r.route_id: _metrics(r.route_id) for r in routes},
        findings=findings or [],
        benchmark=benchmark,
        analyses_run=[],
        analyses_skipped=[],
        metadata={},
    )


def _kinds(gj: dict, kind: str) -> list[dict]:
    return [f for f in gj["features"] if f["properties"]["kind"] == kind]


class TestGeoJSONValidity:
    """It must be GeoJSON a map library will accept."""

    def test_is_a_feature_collection(self) -> None:
        gj = build_routes_geojson(_report())
        assert gj["type"] == "FeatureCollection"
        assert isinstance(gj["features"], list)

    def test_is_json_serializable(self) -> None:
        """It gets written to storage as bytes; anything unserializable is fatal."""
        json.dumps(build_routes_geojson(_report()))

    def test_every_feature_is_well_formed(self) -> None:
        gj = build_routes_geojson(_report())
        for feature in gj["features"]:
            assert feature["type"] == "Feature"
            assert feature["geometry"]["type"] in ("Point", "LineString")
            assert feature["geometry"]["coordinates"]
            assert "kind" in feature["properties"]

    def test_coordinates_are_lon_lat_not_lat_lon(self) -> None:
        """GeoJSON is [lon, lat] — the reverse of this codebase's convention.

        Getting this backwards puts a Dallas fleet in the Indian Ocean, and it
        renders without error, so only an assertion catches it.
        """
        gj = build_routes_geojson(_report())
        stop = _kinds(gj, "stop")[0]
        lon, lat = stop["geometry"]["coordinates"]
        assert -97 < lon < -96, f"expected Dallas longitude, got {lon}"
        assert 32 < lat < 33, f"expected Dallas latitude, got {lat}"

    def test_bbox_is_west_south_east_north(self) -> None:
        gj = build_routes_geojson(_report())
        west, south, east, north = gj["bbox"]
        assert west <= east
        assert south <= north
        assert -97 < west < -96


class TestGeometryHonesty:
    """Straight lines are not road paths, and the artifact must say so."""

    def test_no_provider_falls_back_to_approximate(self) -> None:
        gj = build_routes_geojson(_report())
        assert gj["properties"]["geometry_quality"] == "approximate"

    def test_approximate_note_explains_why(self) -> None:
        note = build_routes_geojson(_report())["properties"]["geometry_note"]
        assert "straight segments" in note

    def test_line_features_carry_their_own_quality(self) -> None:
        """A fleet can be mixed, so quality is per-line as well as collection-level."""
        gj = build_routes_geojson(_report())
        assert _kinds(gj, "actual")[0]["properties"]["geometry_quality"] == "approximate"


class TestPlannedRoute:
    """The plan as uploaded."""

    def test_line_runs_depot_to_stops_to_depot(self) -> None:
        gj = build_routes_geojson(_report(routes=[_route(n=3)]))
        line = _kinds(gj, "actual")[0]
        coords = line["geometry"]["coordinates"]
        assert len(coords) == 5  # depot + 3 stops + depot
        assert coords[0] == coords[-1], "route must return to the depot"

    def test_carries_route_metrics(self) -> None:
        gj = build_routes_geojson(_report())
        props = _kinds(gj, "actual")[0]["properties"]
        assert props["route_id"] == "R1"
        assert props["stop_count"] == 3
        assert props["total_distance_miles"] == 12.5
        assert props["sequencing_index"] == 1.42

    def test_one_line_per_route(self) -> None:
        gj = build_routes_geojson(_report(routes=[_route("R1"), _route("R2")]))
        assert len(_kinds(gj, "actual")) == 2


class TestStopsAndDepots:
    def test_one_point_per_stop(self) -> None:
        gj = build_routes_geojson(_report(routes=[_route(n=4)]))
        assert len(_kinds(gj, "stop")) == 4

    def test_stop_carries_window_and_identity(self) -> None:
        route = _route(n=1)
        route.stops[0].customer_id = "ACME"
        route.stops[0].time_window_start = time(9, 0)
        route.stops[0].time_window_end = time(17, 30)
        gj = build_routes_geojson(_report(routes=[route]))
        props = _kinds(gj, "stop")[0]["properties"]
        assert props["customer_id"] == "ACME"
        assert props["time_window_start"] == "09:00"
        assert props["time_window_end"] == "17:30"

    def test_shared_depot_emits_one_marker(self) -> None:
        """Stacking N identical markers breaks click targets."""
        gj = build_routes_geojson(_report(routes=[_route("R1"), _route("R2"), _route("R3")]))
        depots = _kinds(gj, "depot")
        assert len(depots) == 1
        assert depots[0]["properties"]["route_ids"] == ["R1", "R2", "R3"]

    def test_distinct_depots_emit_separate_markers(self) -> None:
        gj = build_routes_geojson(
            _report(
                routes=[_route("R1", depot=(32.79, -96.80)), _route("R2", depot=(32.99, -96.60))]
            )
        )
        assert len(_kinds(gj, "depot")) == 2


class TestFindingLinks:
    """Clicking a finding must be able to highlight its features."""

    def _finding(
        self, route_ids: list[str], stop_seqs: list[tuple[str, int]] | None = None
    ) -> Finding:
        return Finding(
            category="sequencing",
            severity="high",
            confidence=0.9,
            title="t",
            evidence=[
                FindingEvidence(metric_name="m", actual_value=1.0, unit="x"),
            ],
            references=FindingReference(route_ids=route_ids, stop_sequences=stop_seqs or []),
            hypothesis="h",
            suggested_investigation="i",
        )

    def test_route_carries_its_finding_ids(self) -> None:
        finding = self._finding(["R1"])
        gj = build_routes_geojson(_report(findings=[finding]))
        assert _kinds(gj, "actual")[0]["properties"]["finding_ids"] == [finding.finding_id]

    def test_stop_carries_its_finding_ids(self) -> None:
        finding = self._finding(["R1"], [("R1", 2)])
        gj = build_routes_geojson(_report(findings=[finding]))
        by_seq = {f["properties"]["stop_sequence"]: f for f in _kinds(gj, "stop")}
        assert by_seq[2]["properties"]["finding_ids"] == [finding.finding_id]
        assert by_seq[1]["properties"]["finding_ids"] == []

    def test_unreferenced_route_has_no_findings(self) -> None:
        gj = build_routes_geojson(_report())
        assert _kinds(gj, "actual")[0]["properties"]["finding_ids"] == []


def _benchmark(stop_order: list[int], gap: float = 12.0) -> BenchmarkResult:
    return BenchmarkResult(
        per_route={
            "R1": RouteBenchmark(
                route_id="R1",
                actual_distance_miles=12.5,
                optimal_distance_miles=11.0,
                distance_gap_pct=gap,
                actual_time_hours=2.0,
                optimal_time_hours=1.8,
                time_gap_pct=10.0,
                improvement_gap_pct=gap,
                stop_order=stop_order,
            )
        },
        fleet_level=None,
    )


class TestOptimalRoute:
    """The actual-vs-optimal toggle, which is the map's centrepiece."""

    def test_optimal_line_follows_solver_order(self) -> None:
        """stop_order [3,1,2] must draw depot->s3->s1->s2->depot."""
        gj = build_routes_geojson(_report(benchmark=_benchmark([3, 1, 2])))
        line = _kinds(gj, "optimal")[0]
        coords = line["geometry"]["coordinates"]
        # stop i sits at lat 32.80 + 0.01*i
        lats = [c[1] for c in coords[1:-1]]
        assert lats == pytest.approx([32.83, 32.81, 32.82])

    def test_no_optimal_line_without_a_benchmark(self) -> None:
        assert _kinds(build_routes_geojson(_report()), "optimal") == []

    def test_no_optimal_line_without_a_stop_order(self) -> None:
        """A benchmark with no reorderable sequence draws nothing."""
        gj = build_routes_geojson(_report(benchmark=_benchmark([])))
        assert _kinds(gj, "optimal") == []

    def test_negative_gap_survives_to_the_map(self) -> None:
        """A plan the solver cannot beat must not be clamped (Phase 10.5 Part B)."""
        gj = build_routes_geojson(_report(benchmark=_benchmark([1, 2, 3], gap=-4.0)))
        assert _kinds(gj, "optimal")[0]["properties"]["distance_gap_pct"] == -4.0
        assert _kinds(gj, "actual")[0]["properties"]["distance_gap_pct"] == -4.0

    def test_collection_flags_benchmark_presence(self) -> None:
        assert build_routes_geojson(_report())["properties"]["has_benchmark"] is False
        assert (
            build_routes_geojson(_report(benchmark=_benchmark([1, 2, 3])))["properties"][
                "has_benchmark"
            ]
            is True
        )


class TestMigrations:
    """Arrows showing which route should serve a stop."""

    def _fleet_benchmark(self) -> BenchmarkResult:
        return BenchmarkResult(
            per_route={},
            fleet_level=FleetBenchmark(
                actual_total_distance=25.0,
                optimal_total_distance=22.0,
                stop_migrations=[
                    StopMigration(
                        route_id="R1",
                        stop_sequence=2,
                        customer_id="ACME",
                        from_route="R1",
                        to_route="R2",
                    )
                ],
                improvement_gap_pct=12.0,
            ),
        )

    def test_migration_arrow_points_from_stop_to_target_depot(self) -> None:
        gj = build_routes_geojson(
            _report(
                routes=[_route("R1", depot=(32.79, -96.80)), _route("R2", depot=(32.99, -96.60))],
                benchmark=self._fleet_benchmark(),
            )
        )
        arrows = _kinds(gj, "migration")
        assert len(arrows) == 1
        coords = arrows[0]["geometry"]["coordinates"]
        assert coords[0] == pytest.approx([-96.80, 32.82])  # stop 2 of R1
        assert coords[1] == pytest.approx([-96.60, 32.99])  # R2's depot
        assert arrows[0]["properties"]["to_route"] == "R2"

    def test_no_arrows_without_a_fleet_benchmark(self) -> None:
        assert _kinds(build_routes_geojson(_report()), "migration") == []

    def test_migration_to_an_unknown_route_is_skipped(self) -> None:
        """Never emit an arrow to a route that isn't in the fleet."""
        bm = self._fleet_benchmark()
        assert bm.fleet_level is not None
        bm.fleet_level.stop_migrations[0].to_route = "R_GHOST"
        gj = build_routes_geojson(_report(routes=[_route("R1")], benchmark=bm))
        assert _kinds(gj, "migration") == []


class TestEmptyFleet:
    def test_no_stops_does_not_crash(self) -> None:
        route = Route(
            route_id="R1",
            stops=[],
            depot_lat=32.79,
            depot_lon=-96.80,
            planned_start_time=datetime(2025, 1, 15, 8, 0, tzinfo=UTC),
        )
        gj = build_routes_geojson(_report(routes=[route]))
        assert gj["type"] == "FeatureCollection"
        assert _kinds(gj, "depot")
