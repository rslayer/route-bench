"""Emit routes.geojson — the map artifact the web UI renders.

The UI renders geography; it never computes it. Everything the map needs is in
this file: route lines (actual and solver-optimal), stops, depots, and migration
links, each carrying the identifiers needed to link a finding to a feature.

Geometry comes from OSRM /route where available and degrades to straight
segments where not. The collection reports which it got via `geometry_quality`,
and individual line features carry their own — a fleet can be mixed, because a
single unroutable route falls back on its own without dragging the rest down.

The distances and times in these properties always come from the matrix and are
real road figures regardless of geometry quality. Under "approximate" geometry
those numbers visibly disagree with the drawn line, which is exactly why the
flag has to reach the UI.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from routebench.core.findings import AnalysisReport
from routebench.core.schemas import Route
from routebench.infra.geometry import RouteGeometry, straight_line

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# GeoJSON is [longitude, latitude] — the reverse of how the rest of this
# codebase passes coordinates around. Every conversion goes through _pos so the
# swap happens in exactly one place.
Position = list[float]


class GeometryProvider(Protocol):
    """Structural type for a geometry source (see infra.geometry)."""

    def fetch(self, coords: list[tuple[float, float]]) -> RouteGeometry: ...


def _pos(lat: float, lon: float) -> Position:
    return [lon, lat]


def _actual_waypoints(route: Route) -> list[tuple[float, float]]:
    """Depot -> stops in planned order -> depot, as (lat, lon)."""
    return [
        (route.depot_lat, route.depot_lon),
        *[(s.latitude, s.longitude) for s in route.stops],
        (route.depot_lat, route.depot_lon),
    ]


def _optimal_waypoints(route: Route, stop_order: list[int]) -> list[tuple[float, float]]:
    """Depot -> stops in solver order -> depot.

    `stop_order` holds matrix indices (1..n; 0 is the depot), which is how the
    solver reports its tour.
    """
    waypoints = [(route.depot_lat, route.depot_lon)]
    for idx in stop_order:
        stop_idx = idx - 1
        if 0 <= stop_idx < len(route.stops):
            stop = route.stops[stop_idx]
            waypoints.append((stop.latitude, stop.longitude))
    waypoints.append((route.depot_lat, route.depot_lon))
    return waypoints


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _line(coords: list[Position], properties: dict[str, Any]) -> dict[str, Any]:
    return _feature({"type": "LineString", "coordinates": coords}, properties)


def _point(coord: Position, properties: dict[str, Any]) -> dict[str, Any]:
    return _feature({"type": "Point", "coordinates": coord}, properties)


def _findings_by_route(report: AnalysisReport) -> dict[str, list[str]]:
    """route_id -> finding_ids, so clicking a finding can highlight its routes."""
    index: dict[str, list[str]] = {}
    for finding in report.findings:
        for route_id in finding.references.route_ids:
            index.setdefault(route_id, []).append(finding.finding_id)
    return index


def _findings_by_stop(report: AnalysisReport) -> dict[tuple[str, int], list[str]]:
    """(route_id, stop_sequence) -> finding_ids, for stop-level highlighting."""
    index: dict[tuple[str, int], list[str]] = {}
    for finding in report.findings:
        for route_id, seq in finding.references.stop_sequences:
            index.setdefault((route_id, seq), []).append(finding.finding_id)
    return index


def _compliance_routes(report: AnalysisReport) -> set[str]:
    """Routes carrying a compliance finding, for the stop violation badge."""
    return {
        route_id
        for finding in report.findings
        if finding.category == "compliance"
        for route_id in finding.references.route_ids
    }


def _bbox(features: list[dict[str, Any]]) -> list[float] | None:
    """[west, south, east, north] over every coordinate, for initial map fit."""
    lons: list[float] = []
    lats: list[float] = []
    for feature in features:
        geom = feature["geometry"]
        coords = geom["coordinates"]
        positions = [coords] if geom["type"] == "Point" else coords
        for lon, lat in positions:
            lons.append(lon)
            lats.append(lat)
    if not lons:
        return None
    return [min(lons), min(lats), max(lons), max(lats)]


def build_routes_geojson(
    report: AnalysisReport,
    geometry_provider: GeometryProvider | None = None,
) -> dict[str, Any]:
    """Build the map artifact for an analysis.

    Returns a FeatureCollection. Every feature carries a `kind` discriminator:

      "actual"     LineString  the plan as uploaded
      "optimal"    LineString  the solver's tour (only where benchmarked)
      "stop"       Point       one per stop
      "depot"      Point       one per distinct depot coordinate
      "migration"  LineString  from a stop to the route that should serve it

    With no `geometry_provider`, every line is straight segments — the shape a
    caller gets when OSRM is not available at all (tests, offline runs).
    """
    features: list[dict[str, Any]] = []
    route_findings = _findings_by_route(report)
    stop_findings = _findings_by_stop(report)
    compliance_routes = _compliance_routes(report)
    benchmark = report.benchmark

    def _geometry(coords: list[tuple[float, float]]) -> RouteGeometry:
        if geometry_provider is None:
            return straight_line(coords)
        return geometry_provider.fetch(coords)

    qualities: list[str] = []

    for route in report.fleet.routes:
        rid = route.route_id
        metrics = report.route_metrics.get(rid)
        route_benchmark = benchmark.per_route.get(rid) if benchmark else None

        actual = _geometry(_actual_waypoints(route))
        qualities.append(actual.quality)
        features.append(
            _line(
                actual.positions,
                {
                    "kind": "actual",
                    "route_id": rid,
                    "geometry_quality": actual.quality,
                    "stop_count": len(route.stops),
                    "finding_ids": route_findings.get(rid, []),
                    "total_distance_miles": (
                        round(metrics.total_distance_miles, 3) if metrics else None
                    ),
                    "total_time_hours": (round(metrics.total_time_hours, 3) if metrics else None),
                    "sequencing_index": (
                        round(metrics.sequencing_index, 4)
                        if metrics and metrics.sequencing_index is not None
                        else None
                    ),
                    # Negative means the solver found nothing better — a real
                    # outcome the UI must render as "within solver reach", not
                    # as a saving. See Phase 10.5 Part B.
                    "distance_gap_pct": (
                        round(route_benchmark.distance_gap_pct, 2) if route_benchmark else None
                    ),
                },
            )
        )

        if route_benchmark is not None and route_benchmark.stop_order:
            optimal = _geometry(_optimal_waypoints(route, route_benchmark.stop_order))
            qualities.append(optimal.quality)
            features.append(
                _line(
                    optimal.positions,
                    {
                        "kind": "optimal",
                        "route_id": rid,
                        "geometry_quality": optimal.quality,
                        "stop_order": route_benchmark.stop_order,
                        "finding_ids": route_findings.get(rid, []),
                        "total_distance_miles": round(route_benchmark.optimal_distance_miles, 3),
                        "total_time_hours": round(route_benchmark.optimal_time_hours, 3),
                        "distance_gap_pct": round(route_benchmark.distance_gap_pct, 2),
                        "improvement_gap_pct": round(route_benchmark.improvement_gap_pct, 2),
                    },
                )
            )

        route_has_violation = rid in compliance_routes
        for stop in route.stops:
            stop_finding_ids = stop_findings.get((rid, stop.stop_sequence), [])
            features.append(
                _point(
                    _pos(stop.latitude, stop.longitude),
                    {
                        "kind": "stop",
                        "route_id": rid,
                        "stop_sequence": stop.stop_sequence,
                        "customer_id": stop.customer_id,
                        "address": stop.address,
                        "stop_type": stop.stop_type,
                        "service_time_minutes": stop.service_time_minutes,
                        "planned_arrival_time": (
                            stop.planned_arrival_time.isoformat()
                            if stop.planned_arrival_time
                            else None
                        ),
                        "time_window_start": (
                            stop.time_window_start.strftime("%H:%M")
                            if stop.time_window_start
                            else None
                        ),
                        "time_window_end": (
                            stop.time_window_end.strftime("%H:%M") if stop.time_window_end else None
                        ),
                        "demand_units": stop.demand_units,
                        # Route-level: compliance findings name the route, not
                        # the individual late stop, so this badges every stop on
                        # a route with a violation rather than pinpointing one.
                        "has_violation": route_has_violation,
                        "finding_ids": stop_finding_ids,
                    },
                )
            )

    # Depots dedupe by coordinate: a shared-depot fleet must not stack N
    # identical markers, which would render as one and break click targets.
    seen_depots: dict[tuple[float, float], list[str]] = {}
    for route in report.fleet.routes:
        seen_depots.setdefault((route.depot_lat, route.depot_lon), []).append(route.route_id)
    for (lat, lon), route_ids in seen_depots.items():
        features.append(
            _point(
                _pos(lat, lon),
                {"kind": "depot", "route_ids": sorted(route_ids)},
            )
        )

    if benchmark is not None and benchmark.fleet_level is not None:
        stop_index = {
            (r.route_id, s.stop_sequence): s for r in report.fleet.routes for s in r.stops
        }
        route_depots = {r.route_id: (r.depot_lat, r.depot_lon) for r in report.fleet.routes}

        for migration in benchmark.fleet_level.stop_migrations:
            migrated_stop = stop_index.get((migration.route_id, migration.stop_sequence))
            target_depot = route_depots.get(migration.to_route)
            if migrated_stop is None or target_depot is None:
                continue
            # A link from the stop toward the depot of the route the solver
            # would rather serve it from: enough to show the pull, without
            # implying a drivable path. Deliberately straight — this is a
            # relationship, not a route.
            features.append(
                _line(
                    [_pos(migrated_stop.latitude, migrated_stop.longitude), _pos(*target_depot)],
                    {
                        "kind": "migration",
                        "route_id": migration.route_id,
                        "stop_sequence": migration.stop_sequence,
                        "customer_id": migration.customer_id,
                        "from_route": migration.from_route,
                        "to_route": migration.to_route,
                        "finding_ids": route_findings.get(migration.route_id, []),
                    },
                )
            )

    # One bad route degrades only itself, so report the fleet honestly: exact
    # only when every line is exact.
    collection_quality = (
        "exact" if qualities and all(q == "exact" for q in qualities) else "approximate"
    )

    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "schema_version": 1,
            "geometry_quality": collection_quality,
            "geometry_note": (
                "Route lines follow OSRM road geometry."
                if collection_quality == "exact"
                else (
                    "Some or all route lines are straight segments between stops rather "
                    "than driven road paths. Distances and times are road-network values "
                    "from OSRM regardless."
                )
            ),
            "has_benchmark": benchmark is not None,
            "has_fleet_benchmark": benchmark is not None and benchmark.fleet_level is not None,
            "route_count": len(report.fleet.routes),
            "stop_count": report.fleet.total_stops(),
        },
    }

    bbox = _bbox(features)
    if bbox is not None:
        collection["bbox"] = bbox

    logger.info(
        "routes_geojson_built",
        features=len(features),
        routes=len(report.fleet.routes),
        geometry_quality=collection_quality,
        has_benchmark=benchmark is not None,
    )
    return collection
