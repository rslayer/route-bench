"""Scorecard orchestrator: compute FleetMetrics and per-route RouteMetrics."""

from __future__ import annotations

import statistics

import structlog

from routebench.analysis.scoring.compliance import compute_compliance_metrics
from routebench.analysis.scoring.density import compute_density_metrics
from routebench.analysis.scoring.distance import (
    compute_distance_metrics,
    get_route_matrix,
)
from routebench.analysis.scoring.sequencing_index import (
    compute_sequencing_index,
)
from routebench.analysis.scoring.time import compute_time_metrics
from routebench.analysis.scoring.utilization import compute_utilization_metrics
from routebench.core.config import AnalysisConfig
from routebench.core.findings import FleetMetrics, RouteMetrics
from routebench.core.schemas import Fleet
from routebench.infra.matrix.base import MatrixProvider

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _f(v: object) -> float:
    """Safely extract a float from a dict value."""
    if isinstance(v, (int, float)):
        return float(v)
    return 0.0


def _i(v: object) -> int:
    """Safely extract an int from a dict value."""
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return 0


def compute_scorecard(
    fleet: Fleet,
    matrix_provider: MatrixProvider,
    config: AnalysisConfig,
) -> tuple[FleetMetrics, dict[str, RouteMetrics]]:
    """Compute the full descriptive scorecard for a fleet.

    Orchestrates per-route metric computation and aggregates fleet-level metrics.

    Returns:
        (FleetMetrics, dict mapping route_id -> RouteMetrics)
    """
    work_rules = config.work_rules
    route_metrics_map: dict[str, RouteMetrics] = {}

    total_distance = 0.0
    total_time = 0.0
    total_stops = 0
    routes_over_shift = 0
    sequencing_indices: list[float] = []
    all_utilizations: dict[str, list[float]] = {}

    for route in fleet.routes:
        logger.info("scoring_route", route_id=route.route_id)

        # Get the matrix for this route
        matrix = get_route_matrix(route, matrix_provider)

        # Distance metrics
        dist_metrics = compute_distance_metrics(route, matrix)

        # Time metrics
        time_metrics = compute_time_metrics(route, matrix, work_rules)

        # Density metrics
        density_metrics = compute_density_metrics(route, dist_metrics, time_metrics)

        # Utilization metrics
        util_metrics = compute_utilization_metrics(route)

        # Compliance metrics
        compliance_metrics = compute_compliance_metrics(
            route, time_metrics, work_rules
        )

        # Sequencing index
        seq_index = compute_sequencing_index(route, matrix)

        # Build RouteMetrics
        total_dist_miles = _f(dist_metrics["total_distance_miles"])
        total_time_hrs = _f(time_metrics["total_time_hours"])
        drive_time_hrs = _f(time_metrics["drive_time_hours"])
        svc_time_hrs = _f(time_metrics["service_time_hours"])
        idle_time_hrs = _f(time_metrics["idle_time_hours"])
        stops_per_hr = _f(density_metrics["stops_per_hour"])
        shift_overrun = _f(compliance_metrics["shift_overrun_minutes"])
        tw_violations = _i(compliance_metrics["time_window_violations"])
        cap_util: dict[str, float] = util_metrics["capacity_utilization"]  # type: ignore[assignment]

        rm = RouteMetrics(
            route_id=route.route_id,
            total_distance_miles=total_dist_miles,
            total_time_hours=total_time_hrs,
            drive_time_hours=drive_time_hrs,
            service_time_hours=svc_time_hrs,
            idle_time_hours=idle_time_hrs,
            stop_count=len(route.stops),
            stops_per_hour=stops_per_hr,
            sequencing_index=seq_index,
            capacity_utilization=cap_util,
            time_window_violations=tw_violations,
            shift_overrun_minutes=shift_overrun,
        )

        if seq_index is not None:
            sequencing_indices.append(seq_index)
        route_metrics_map[route.route_id] = rm

        # Accumulate fleet totals
        total_distance += total_dist_miles
        total_time += total_time_hrs
        total_stops += len(route.stops)
        if shift_overrun > 0:
            routes_over_shift += 1

        # Collect utilizations for fleet averaging
        for dim, val in cap_util.items():
            all_utilizations.setdefault(dim, []).append(val)

    # Compute fleet-level metrics
    avg_utilization: dict[str, float] = {}
    for dim, vals in all_utilizations.items():
        avg_utilization[dim] = sum(vals) / len(vals) if vals else 0.0

    median_seq = (
        statistics.median(sequencing_indices) if sequencing_indices else None
    )

    fleet_metrics = FleetMetrics(
        total_routes=len(fleet.routes),
        total_stops=total_stops,
        total_distance_miles=total_distance,
        total_time_hours=total_time,
        median_sequencing_index=median_seq,
        routes_over_shift_cap=routes_over_shift,
        avg_capacity_utilization=avg_utilization,
    )

    logger.info(
        "scorecard_complete",
        total_routes=fleet_metrics.total_routes,
        total_stops=fleet_metrics.total_stops,
        total_distance_miles=round(fleet_metrics.total_distance_miles, 1),
    )

    return fleet_metrics, route_metrics_map
