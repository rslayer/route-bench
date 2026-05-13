"""Generate synthetic route CSV data for development.

Usage:
    uv run python scripts/generate_synthetic.py --n-routes 5 --output data/synthetic/sample.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

METRO_BBOXES: dict[str, tuple[float, float, float, float]] = {
    # (min_lat, max_lat, min_lon, max_lon)
    "dallas": (32.60, 33.05, -97.05, -96.50),
    "austin": (30.10, 30.55, -97.95, -97.50),
    "houston": (29.55, 30.05, -95.70, -95.10),
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two points in miles."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return 3958.8 * c


def polar_angle(cx: float, cy: float, px: float, py: float) -> float:
    """Compute polar angle from centroid (cx, cy) to point (px, py)."""
    return math.atan2(py - cy, px - cx)


def kmeans_assign(
    points: list[tuple[float, float]], k: int, rng: random.Random, max_iter: int = 50
) -> list[int]:
    """Simple k-means clustering. Returns cluster assignment per point."""
    n = len(points)
    if n <= k:
        return list(range(n))

    # Initialize centroids randomly
    centroid_indices = rng.sample(range(n), k)
    centroids = [points[i] for i in centroid_indices]
    assignments = [0] * n

    for _ in range(max_iter):
        # Assign each point to nearest centroid
        new_assignments = []
        for lat, lon in points:
            dists = [(lat - clat) ** 2 + (lon - clon) ** 2 for clat, clon in centroids]
            new_assignments.append(dists.index(min(dists)))
        if new_assignments == assignments:
            break
        assignments = new_assignments

        # Recompute centroids
        for ci in range(k):
            members = [(points[j][0], points[j][1]) for j in range(n) if assignments[j] == ci]
            if members:
                centroids[ci] = (
                    sum(m[0] for m in members) / len(members),
                    sum(m[1] for m in members) / len(members),
                )

    return assignments


def generate_synthetic(
    n_routes: int = 10,
    avg_stops_per_route: int = 30,
    metro: str = "dallas",
    output: str = "data/synthetic/sample.csv",
    include_time_windows: bool = False,
    include_demand: bool = False,
    seed: int = 42,
) -> Path:
    """Generate a synthetic CSV dataset."""
    rng = random.Random(seed)
    bbox = METRO_BBOXES[metro]
    min_lat, max_lat, min_lon, max_lon = bbox

    # Depot at metro center
    depot_lat = (min_lat + max_lat) / 2
    depot_lon = (min_lon + max_lon) / 2

    # Generate all stops uniformly in the bounding box
    total_stops = n_routes * avg_stops_per_route
    stops = [
        (rng.uniform(min_lat, max_lat), rng.uniform(min_lon, max_lon)) for _ in range(total_stops)
    ]

    # Assign to routes via k-means
    assignments = kmeans_assign(stops, n_routes, rng)

    # Group stops by route
    route_stops: dict[int, list[tuple[float, float]]] = {}
    for i, cluster in enumerate(assignments):
        route_stops.setdefault(cluster, []).append(stops[i])

    # Sort within each cluster by polar angle from cluster centroid
    for _cluster_id, cluster_stops in route_stops.items():
        cx = sum(s[0] for s in cluster_stops) / len(cluster_stops)
        cy = sum(s[1] for s in cluster_stops) / len(cluster_stops)
        cluster_stops.sort(key=lambda p: polar_angle(cx, cy, p[0], p[1]))

    # Build CSV rows
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "route_id",
        "stop_sequence",
        "latitude",
        "longitude",
        "stop_type",
        "planned_arrival_time",
        "service_time_minutes",
        "planned_start_time",
        "vehicle_capacity_units",
    ]
    if include_time_windows:
        fieldnames.extend(["time_window_start", "time_window_end"])
    if include_demand:
        fieldnames.append("demand_units")

    rows: list[dict[str, object]] = []

    for route_idx, (_cluster_id, cluster_stops) in enumerate(sorted(route_stops.items())):
        route_id = f"R{route_idx + 1:03d}"
        start_hour = 7 + rng.randint(0, 2)
        start_time = datetime(2025, 1, 15, start_hour, 0, 0, tzinfo=UTC)
        capacity = rng.randint(50, 200)

        # Depot row (stop_sequence=0)
        depot_row: dict[str, object] = {
            "route_id": route_id,
            "stop_sequence": 0,
            "latitude": round(depot_lat, 6),
            "longitude": round(depot_lon, 6),
            "stop_type": "depot",
            "planned_arrival_time": "",
            "service_time_minutes": 0,
            "planned_start_time": start_time.isoformat(),
            "vehicle_capacity_units": capacity,
        }
        if include_time_windows:
            depot_row["time_window_start"] = ""
            depot_row["time_window_end"] = ""
        if include_demand:
            depot_row["demand_units"] = 0
        rows.append(depot_row)

        # Stop rows
        current_time = start_time
        prev_lat, prev_lon = depot_lat, depot_lon
        for seq, (slat, slon) in enumerate(cluster_stops, start=1):
            dist = haversine_miles(prev_lat, prev_lon, slat, slon)
            travel_hours = dist / 30.0  # 30 mph
            current_time += timedelta(hours=travel_hours)

            service_minutes = rng.uniform(3.0, 10.0)

            stop_row: dict[str, object] = {
                "route_id": route_id,
                "stop_sequence": seq,
                "latitude": round(slat, 6),
                "longitude": round(slon, 6),
                "stop_type": "delivery",
                "planned_arrival_time": current_time.isoformat(),
                "service_time_minutes": round(service_minutes, 1),
                "planned_start_time": start_time.isoformat(),
                "vehicle_capacity_units": capacity,
            }

            if include_time_windows:
                window_start = current_time - timedelta(minutes=30)
                window_end = current_time + timedelta(minutes=60)
                stop_row["time_window_start"] = window_start.strftime("%H:%M:%S")
                stop_row["time_window_end"] = window_end.strftime("%H:%M:%S")

            if include_demand:
                stop_row["demand_units"] = rng.randint(1, 10)

            rows.append(stop_row)

            current_time += timedelta(minutes=service_minutes)
            prev_lat, prev_lon = slat, slon

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Generated {len(rows)} rows ({n_routes} routes) -> {out_path}")
    return out_path


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate synthetic route CSV data")
    parser.add_argument("--n-routes", type=int, default=10)
    parser.add_argument("--avg-stops-per-route", type=int, default=30)
    parser.add_argument("--metro", choices=list(METRO_BBOXES.keys()), default="dallas")
    parser.add_argument("--output", type=str, default="data/synthetic/sample.csv")
    parser.add_argument("--include-time-windows", action="store_true")
    parser.add_argument("--include-demand", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    generate_synthetic(
        n_routes=args.n_routes,
        avg_stops_per_route=args.avg_stops_per_route,
        metro=args.metro,
        output=args.output,
        include_time_windows=args.include_time_windows,
        include_demand=args.include_demand,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
    sys.exit(0)
