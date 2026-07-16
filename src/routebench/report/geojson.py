"""Emit routes.geojson — the map artifact the web UI renders.

The UI renders geography; it never computes it. Everything the map needs is in
this file: route lines (planned and solver-optimal), stops, depots, and
migration arrows, each carrying the identifiers needed to link a finding to a
feature.

GEOMETRY IS APPROXIMATE. The matrix provider fetches OSRM `/table` (travel-time
and distance matrices), not `/route` (road polylines), so no road geometry
exists anywhere in the pipeline. Lines here are straight segments between
consecutive stops — correct in topology and order, but not the path a vehicle
drives. `geometry_approximate: true` on the collection says so, and the UI is
expected to surface it. Upgrading to real road paths means adding an OSRM
/route call per leg, which is a separate piece of work.

Distances and times shown alongside the map come from the matrices and ARE road
distances; only the drawn line is approximate. Those two facts sitting next to
each other is exactly why the flag needs to be explicit.
"""

from __future__ import annotations

from typing import Any

import structlog

from routebench.core.findings import AnalysisReport
from routebench.core.schemas import Route

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# GeoJSON is [longitude, latitude] — the reverse of how the rest of this
# codebase passes coordinates around. Every conversion goes through _pos so the
# swap happens in exactly one place.
Position = list[float]


def _pos(lat: float, lon: float) -> Position:
    return [lon, lat]


def _route_positions(route: Route) -> list[Position]:
    """Depot -> stops in planned order -> depot."""
    coords = [_pos(route.depot_lat, route.depot_lon)]
    coords.extend(_pos(s.latitude, s.longitude) for s in route.stops)
    coords.append(_pos(route.depot_lat, route.depot_lon))
    return coords


def _optimal_positions(route: Route, stop_order: list[int]) -> list[Position]:
    """Depot -> stops in solver order -> depot.

    `stop_order` holds matrix indices (1..n; 0 is the depot), which is how the
    solver reports its tour.
    """
    coords = [_pos(route.depot_lat, route.depot_lon)]
    for idx in stop_order:
        stop_idx = idx - 1
        if 0 <= stop_idx < len(route.stops):
            stop = route.stops[stop_idx]
            coords.append(_pos(stop.latitude, stop.longitude))
    coords.append(_pos(route.depot_lat, route.depot_lon))
    return coords


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


def build_routes_geojson(report: AnalysisReport) -> dict[str, Any]:
    """Build the map artifact for an analysis.

    Returns a FeatureCollection. Every feature carries a `kind` discriminator:

      "route_planned"  LineString  the plan as uploaded
      "route_optimal"  LineString  the solver's tour (only where benchmarked)
      "stop"           Point       one per stop
      "depot"          Point       one per route's depot (deduplicated)
      "migration"      LineString  from a stop to the route that should serve it

    Collection-level `properties` carry `geometry_approximate` and the counts a
    UI needs before it renders anything.
    """
    features: list[dict[str, Any]] = []
    route_findings = _findings_by_route(report)
    stop_findings = _findings_by_stop(report)
    benchmark = report.benchmark

    for route in report.fleet.routes:
        rid = route.route_id
        metrics = report.route_metrics.get(rid)
        route_benchmark = benchmark.per_route.get(rid) if benchmark else None

        features.append(
            _line(
                _route_positions(route),
                {
                    "kind": "route_planned",
                    "route_id": rid,
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
            features.append(
                _line(
                    _optimal_positions(route, route_benchmark.stop_order),
                    {
                        "kind": "route_optimal",
                        "route_id": rid,
                        "total_distance_miles": round(route_benchmark.optimal_distance_miles, 3),
                        "total_time_hours": round(route_benchmark.optimal_time_hours, 3),
                        "distance_gap_pct": round(route_benchmark.distance_gap_pct, 2),
                        "improvement_gap_pct": round(route_benchmark.improvement_gap_pct, 2),
                    },
                )
            )

        for stop in route.stops:
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
                        "time_window_start": (
                            stop.time_window_start.strftime("%H:%M")
                            if stop.time_window_start
                            else None
                        ),
                        "time_window_end": (
                            stop.time_window_end.strftime("%H:%M") if stop.time_window_end else None
                        ),
                        "finding_ids": stop_findings.get((rid, stop.stop_sequence), []),
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
            (r.route_id, s.stop_sequence): (s, r) for r in report.fleet.routes for s in r.stops
        }
        route_depots = {r.route_id: (r.depot_lat, r.depot_lon) for r in report.fleet.routes}

        for migration in benchmark.fleet_level.stop_migrations:
            entry = stop_index.get((migration.route_id, migration.stop_sequence))
            target_depot = route_depots.get(migration.to_route)
            if entry is None or target_depot is None:
                continue
            stop, _route = entry
            # An arrow from the stop toward the depot of the route the solver
            # would rather serve it from: enough to show the pull, without
            # implying a drivable path.
            features.append(
                _line(
                    [_pos(stop.latitude, stop.longitude), _pos(*target_depot)],
                    {
                        "kind": "migration",
                        "route_id": migration.route_id,
                        "stop_sequence": migration.stop_sequence,
                        "customer_id": migration.customer_id,
                        "from_route": migration.from_route,
                        "to_route": migration.to_route,
                    },
                )
            )

    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "schema_version": 1,
            # Straight segments between stops, not driven road paths. The UI is
            # expected to surface this; distances/times alongside ARE real road
            # figures from the matrix, which is why the distinction matters.
            "geometry_approximate": True,
            "geometry_note": (
                "Route lines are straight segments between consecutive stops, not "
                "driven road paths. Distances and times are road-network values "
                "from OSRM."
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
        has_benchmark=benchmark is not None,
    )
    return collection
