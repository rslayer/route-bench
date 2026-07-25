#!/usr/bin/env python3
"""Generate a diverse set of test fleets for RouteBench.

`scripts/generate_sample_fleet.py` builds the ONE hand-tuned Dallas fleet the
committed sample report comes from. This script is different: it emits several
fleets across different North American metros so the pipeline can be exercised
against varied geography, size, density, and constraints — not just one shape.

Deterministic (no RNG): regenerating produces byte-identical CSVs. Coordinates
are scattered within each metro's road network, so the Google matrix/geometry
engine snaps them to real streets. They are illustrative fixtures, not audited
real delivery data.

    uv run python scripts/generate_test_fleets.py
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "samples" / "test_fleets"

# Golden angle — spreads points evenly and non-collinearly without an RNG, so a
# route never collapses to a straight line of stops.
_GOLDEN = 2.399963229728653

_HEADER = [
    "route_id",
    "stop_sequence",
    "latitude",
    "longitude",
    "stop_type",
    "planned_arrival_time",
    "service_time_minutes",
    "planned_start_time",
    "vehicle_capacity_units",
    "time_window_start",
    "time_window_end",
    "demand_units",
    "customer_id",
]


@dataclass(frozen=True)
class FleetSpec:
    slug: str
    metro: str
    center: tuple[float, float]
    radius_deg: float  # spread of the whole fleet around the depot
    stops_per_route: tuple[int, ...]
    start_times: tuple[str, ...]  # "HH:MM" wall clock, one per route
    date: str  # "YYYY-MM-DD"
    capacity: float
    demand_per_stop: float
    service_min: float
    time_windows: bool
    pickups: bool
    purpose: str


FLEETS: tuple[FleetSpec, ...] = (
    FleetSpec(
        slug="01_urban_dense_nyc",
        metro="New York City (Manhattan / Brooklyn)",
        center=(40.7420, -73.9890),
        radius_deg=0.045,  # ~3 mi: tight, high-density last mile
        stops_per_route=(13, 12, 14, 12),
        start_times=("07:00", "07:05", "07:10", "07:15"),  # clustered dispatch
        date="2025-06-10",
        capacity=80,
        demand_per_stop=5,  # comfortably under capacity
        service_min=6,
        time_windows=False,
        pickups=False,
        purpose="Dense urban grid, many stops per route, clustered dispatch times.",
    ),
    FleetSpec(
        slug="02_rural_sparse_montana",
        metro="Bozeman / Gallatin Valley, MT",
        center=(45.6800, -111.0400),
        radius_deg=0.28,  # ~18 mi: long rural legs
        stops_per_route=(5, 4, 6),
        start_times=("08:00", "08:00", "08:30"),
        date="2025-06-11",
        capacity=120,
        demand_per_stop=12,
        service_min=10,
        time_windows=False,
        pickups=False,
        purpose="Sparse rural fleet, long inter-stop legs, low stop density.",
    ),
    FleetSpec(
        slug="03_overcapacity_chicago",
        metro="Chicago, IL",
        center=(41.8790, -87.6300),
        radius_deg=0.10,
        stops_per_route=(9, 8, 9, 8, 8),
        start_times=("06:30", "06:35", "06:40", "09:00", "09:05"),
        date="2025-06-12",
        capacity=60,
        demand_per_stop=9,  # 8-9 stops x 9 > 60: over vehicle capacity
        service_min=7,
        time_windows=False,
        pickups=False,
        purpose="Demand exceeds vehicle capacity; exercises over-utilization and rebalancing.",
    ),
    FleetSpec(
        slug="04_timewindows_la",
        metro="Los Angeles, CA",
        center=(34.0500, -118.2450),
        radius_deg=0.12,
        stops_per_route=(7, 8, 7, 6),
        start_times=("13:00", "13:15", "14:00", "15:30"),  # afternoon into peak
        date="2025-06-13",
        capacity=90,
        demand_per_stop=6,
        service_min=8,
        time_windows=True,  # tight windows, some unsatisfiable by the plan order
        pickups=True,  # pickup + delivery mix
        purpose="Tight per-stop time windows (some infeasible), pickup/delivery mix, afternoon.",
    ),
    FleetSpec(
        slug="05_large_route_atlanta",
        metro="Atlanta, GA",
        center=(33.7500, -84.3900),
        radius_deg=0.14,
        stops_per_route=(28, 8),  # 28 > 25 exercises Google geometry chunking
        start_times=("07:00", "07:00"),
        date="2025-06-14",
        capacity=150,
        demand_per_stop=4,
        service_min=5,
        time_windows=False,
        pickups=False,
        purpose="One 28-stop route (geometry chunking, long-route sequencing) beside a small one.",
    ),
)


def _scatter(
    center: tuple[float, float], n: int, radius: float, seed: float
) -> list[tuple[float, float]]:
    """`n` points spread over a disc around `center`, deterministically.

    Radius grows with sqrt(i) for an even areal fill (a phyllotaxis spiral), and
    longitude is scaled by cos(lat) so the spread is roughly circular on the
    ground rather than stretched east-west.
    """
    clat, clon = center
    lon_scale = math.cos(math.radians(clat))
    points: list[tuple[float, float]] = []
    for i in range(n):
        angle = i * _GOLDEN + seed
        r = radius * math.sqrt((i + 0.5) / n)
        lat = clat + r * math.sin(angle)
        lon = clon + (r * math.cos(angle)) / lon_scale
        points.append((round(lat, 5), round(lon, 5)))
    return points


def _route_stops(spec: FleetSpec, route_idx: int, n_stops: int) -> list[tuple[float, float]]:
    """Scatter one route's stops into its own sector around the depot.

    Each route fans into a different sector so territories differ, but the
    sectors overlap enough that cross-route rebalancing has something to find.
    """
    n_routes = len(spec.stops_per_route)
    sector = route_idx * (2 * math.pi / n_routes)
    lon_scale = math.cos(math.radians(spec.center[0]))
    cluster = (
        spec.center[0] + 0.4 * spec.radius_deg * math.sin(sector),
        spec.center[1] + 0.4 * spec.radius_deg * math.cos(sector) / lon_scale,
    )
    return _scatter(cluster, n_stops, spec.radius_deg * 0.6, seed=route_idx * 1.7)


def _windows(spec: FleetSpec, start: str, seq: int) -> tuple[str, str]:
    """A tight [open, close] window that drifts later with stop order.

    Windows open 12 min apart and stay open 40 min. Because real travel plus
    service runs longer than 12 min per stop, later stops in the planned order
    fall outside their windows — which is the point: it gives the compliance
    analysis real violations to find.
    """
    base = datetime.combine(datetime(2000, 1, 1), time.fromisoformat(start))
    open_t = (base + timedelta(minutes=12 * seq)).time()
    close_t = (base + timedelta(minutes=12 * seq + 40)).time()
    return open_t.strftime("%H:%M"), close_t.strftime("%H:%M")


def build_rows(spec: FleetSpec) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for route_idx, n_stops in enumerate(spec.stops_per_route):
        route_id = f"R{route_idx + 1:03d}"
        start = spec.start_times[route_idx]
        start_iso = f"{spec.date}T{start}:00+00:00"
        prefix = spec.slug.split("_", 1)[0]

        # Depot (stop_sequence 0) — shared metro center, no demand, no window.
        rows.append(
            {
                "route_id": route_id,
                "stop_sequence": 0,
                "latitude": spec.center[0],
                "longitude": spec.center[1],
                "stop_type": "depot",
                "planned_arrival_time": "",
                "service_time_minutes": 0,
                "planned_start_time": start_iso,
                "vehicle_capacity_units": spec.capacity,
                "time_window_start": "",
                "time_window_end": "",
                "demand_units": 0,
                "customer_id": "",
            }
        )

        for seq, (lat, lon) in enumerate(_route_stops(spec, route_idx, n_stops), start=1):
            stop_type = ("pickup" if seq % 3 == 0 else "delivery") if spec.pickups else "delivery"
            tw_start, tw_end = _windows(spec, start, seq) if spec.time_windows else ("", "")
            rows.append(
                {
                    "route_id": route_id,
                    "stop_sequence": seq,
                    "latitude": lat,
                    "longitude": lon,
                    "stop_type": stop_type,
                    "planned_arrival_time": "",
                    "service_time_minutes": spec.service_min,
                    "planned_start_time": start_iso,
                    "vehicle_capacity_units": spec.capacity,
                    "time_window_start": tw_start,
                    "time_window_end": tw_end,
                    "demand_units": spec.demand_per_stop,
                    "customer_id": f"{prefix}-{route_id}-{seq:02d}",
                }
            )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for spec in FLEETS:
        rows = build_rows(spec)
        path = OUT_DIR / f"{spec.slug}.csv"
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_HEADER)
            writer.writeheader()
            writer.writerows(rows)
        n_routes = len(spec.stops_per_route)
        n_stops = sum(spec.stops_per_route)
        print(f"{path.name}: {n_routes} routes, {n_stops} stops — {spec.metro}")


if __name__ == "__main__":
    main()
