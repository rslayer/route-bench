"""Generate the hand-curated sample fleet.

Unlike scripts/generate_synthetic.py, which places stops randomly, every route
here is positioned to exercise one specific diagnosis. That makes the sample
report a demonstration of each analysis rather than a random draw that may or
may not trip anything.

Coordinates are real Dallas-area locations so a Texas OSRM extract can route
them. The output is deterministic: no RNG, so regenerating produces byte
identical CSV and the sample report stays reproducible.

What each route is for:

  R001  sequencing   zigzags east-west across a corridor instead of running it
                     in order, so the actual tour is far longer than nearest-
                     neighbour. Also supplies the per-route benchmark gap.
  R002  time_pressure  dispatched at 07:30 for stops that cannot be served
                     until 11:00, so the driver idles for hours.
  R003  outlier      a tight Oak Cliff cluster plus one stop stranded 14 miles
                     east in Mesquite.
  R004  territory    interleaves with R005 across the same North Dallas ground.
  R005  territory    the other half of that overlap; together they also give the
                     fleet benchmark stops worth migrating.
  R006  compliance   more afternoon work than its 16:00 windows allow, running
                     into the peak band.

Dispatch clustering falls out of the start times: five of six routes leave
within 15 minutes of each other, above the tool's 70% threshold.

Usage:
    uv run python scripts/generate_sample_fleet.py
    uv run python scripts/generate_sample_fleet.py --output path/to/fleet.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Dallas — a real depot location, downtown.
DEPOT_LAT = 32.7767
DEPOT_LON = -96.7970

PLAN_DATE = datetime(2025, 3, 11, tzinfo=UTC)
PLAN_SPEED_MPH = 30.0  # what the planner assumed; reality is what we measure


@dataclass
class SampleStop:
    lat: float
    lon: float
    service_minutes: float = 8.0
    window_start: str = ""
    window_end: str = ""
    demand_units: int = 5
    customer_id: str = ""


@dataclass
class SampleRoute:
    route_id: str
    start_hour: int
    start_minute: int
    capacity_units: int
    stops: list[SampleStop] = field(default_factory=list)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat, dlon = lat2_r - lat1_r, lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def _zigzag_route() -> SampleRoute:
    """R001: a corridor run in the worst possible order.

    Every stop sits near lat 32.85; the sequence alternates far-west / far-east
    instead of sweeping. Actual tour distance lands well above the nearest-
    neighbour heuristic, tripping analyze_sequencing (index >= 1.30) and leaving
    a real gap for the TSPTW benchmark to close.
    """
    lons = [-96.90, -96.70, -96.88, -96.72, -96.86, -96.74, -96.84, -96.76]
    return SampleRoute(
        route_id="R001",
        start_hour=7,
        start_minute=30,
        capacity_units=60,
        stops=[
            SampleStop(lat=32.85, lon=lon, demand_units=7, customer_id=f"C-ZZ-{i:02d}")
            for i, lon in enumerate(lons, start=1)
        ],
    )


def _idle_route() -> SampleRoute:
    """R002: dispatched hours before its stops will accept delivery.

    Stops open at 11:00. Leaving at 07:30 means the driver arrives around 08:00
    and waits, which analyze_time_pressure reads as time-window-driven idle
    (>= 0.5h fires; this produces hours).
    """
    coords = [
        (32.8100, -96.8500),
        (32.8180, -96.8420),
        (32.8250, -96.8350),
        (32.8320, -96.8280),
        (32.8390, -96.8210),
        (32.8460, -96.8140),
    ]
    return SampleRoute(
        route_id="R002",
        start_hour=7,
        start_minute=30,
        capacity_units=80,
        stops=[
            SampleStop(
                lat=lat,
                lon=lon,
                window_start="11:00:00",
                window_end="15:00:00",
                demand_units=4,
                customer_id=f"C-ID-{i:02d}",
            )
            for i, (lat, lon) in enumerate(coords, start=1)
        ],
    )


def _outlier_route() -> SampleRoute:
    """R003: a tight cluster plus one stop stranded far away.

    Six stops sit within ~1 mile of each other in Oak Cliff; the seventh is out
    in Mesquite, ~14 miles east. Its nearest-neighbour distance blows past the
    1.5x-median threshold in analyze_outliers. Needs >= 5 stops to qualify.
    Low total demand against a large vehicle also leaves it visibly
    under-utilised on the scorecard.
    """
    cluster = [
        (32.7450, -96.8300),
        (32.7480, -96.8250),
        (32.7510, -96.8330),
        (32.7440, -96.8210),
        (32.7530, -96.8280),
        (32.7470, -96.8360),
    ]
    stops = [
        SampleStop(lat=lat, lon=lon, demand_units=2, customer_id=f"C-OL-{i:02d}")
        for i, (lat, lon) in enumerate(cluster, start=1)
    ]
    # The stranded stop: Mesquite, ~14 miles from the cluster.
    stops.append(
        SampleStop(lat=32.7668, lon=-96.5992, service_minutes=12.0, customer_id="C-OL-FAR")
    )
    return SampleRoute(
        route_id="R003",
        start_hour=7,
        start_minute=35,
        capacity_units=120,
        stops=stops,
    )


def _overlap_routes() -> list[SampleRoute]:
    """R004/R005: two routes interleaved across the same North Dallas ground.

    Their stops alternate along the same corridor, so each route's convex hull
    swallows a large share of the other's stops — analyze_territory flags an
    overlap above 20%. This is also the material the fleet-level VRPTW needs to
    propose stop migrations.
    """
    corridor = [
        (32.9000, -96.7700),
        (32.9050, -96.7650),
        (32.9100, -96.7600),
        (32.9150, -96.7550),
        (32.9200, -96.7500),
        (32.9250, -96.7450),
        (32.9300, -96.7400),
        (32.9350, -96.7350),
    ]
    even = [c for i, c in enumerate(corridor) if i % 2 == 0]
    odd = [c for i, c in enumerate(corridor) if i % 2 == 1]

    r004 = SampleRoute(
        route_id="R004",
        start_hour=7,
        start_minute=30,
        capacity_units=70,
        stops=[
            SampleStop(lat=lat, lon=lon, demand_units=6, customer_id=f"C-TA-{i:02d}")
            for i, (lat, lon) in enumerate(even, start=1)
        ],
    )
    r005 = SampleRoute(
        route_id="R005",
        start_hour=7,
        start_minute=40,
        capacity_units=70,
        stops=[
            SampleStop(lat=lat, lon=lon, demand_units=6, customer_id=f"C-TB-{i:02d}")
            for i, (lat, lon) in enumerate(odd, start=1)
        ],
    )
    return [r004, r005]


def _tight_window_route() -> SampleRoute:
    """R006: more work than its windows allow, in the afternoon peak.

    Every stop commits to a window closing at 16:00, but the route leaves at
    15:00 and runs 17 miles east with 8 minutes of service per stop. The last
    stops cannot be reached in time, so analyze_compliance fires.

    The route is over-committed rather than marginal on purpose. Tuning it to
    pass under free-flow and fail only under a profile would need the arrival to
    land inside a window a few minutes wide, and OSRM road distances run well
    above the haversine used to check this design offline — the flip would not
    survive the provider swap. The free-flow-vs-profiled delta is proven
    directly in tests/analysis/test_traffic.py instead.

    Starting at 15:00 also puts the later legs inside urban_us's 16:00-18:30
    band, so the afternoon peak has something to act on, and keeps R006 clear of
    the morning cluster so dispatch clustering stays at five of six routes.
    """
    coords = [
        (32.7900, -96.7500),
        (32.7950, -96.7200),
        (32.8000, -96.6900),
        (32.8050, -96.6600),
        (32.8100, -96.6300),
        (32.8150, -96.6000),
        (32.8200, -96.5700),
    ]
    return SampleRoute(
        route_id="R006",
        start_hour=15,
        start_minute=0,
        capacity_units=90,
        stops=[
            SampleStop(
                lat=lat,
                lon=lon,
                service_minutes=8.0,
                window_start="15:00:00",
                window_end="16:00:00",
                demand_units=5,
                customer_id=f"C-TW-{i:02d}",
            )
            for i, (lat, lon) in enumerate(coords, start=1)
        ],
    )


def build_fleet() -> list[SampleRoute]:
    return [
        _zigzag_route(),
        _idle_route(),
        _outlier_route(),
        *_overlap_routes(),
        _tight_window_route(),
    ]


FIELDNAMES = [
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


def _rows_for_route(route: SampleRoute) -> list[dict[str, object]]:
    start = PLAN_DATE.replace(hour=route.start_hour, minute=route.start_minute)
    rows: list[dict[str, object]] = [
        {
            "route_id": route.route_id,
            "stop_sequence": 0,
            "latitude": round(DEPOT_LAT, 6),
            "longitude": round(DEPOT_LON, 6),
            "stop_type": "depot",
            "planned_arrival_time": "",
            "service_time_minutes": 0,
            "planned_start_time": start.isoformat(),
            "vehicle_capacity_units": route.capacity_units,
            "time_window_start": "",
            "time_window_end": "",
            "demand_units": 0,
            "customer_id": "",
        }
    ]

    # planned_arrival_time is what the planner believed, at a flat assumed speed.
    # It is deliberately naive: the gap between it and measured road time is part
    # of what the report surfaces. Nothing grades against this column.
    current = start
    prev_lat, prev_lon = DEPOT_LAT, DEPOT_LON
    for seq, stop in enumerate(route.stops, start=1):
        miles = haversine_miles(prev_lat, prev_lon, stop.lat, stop.lon)
        current += timedelta(hours=miles / PLAN_SPEED_MPH)
        rows.append(
            {
                "route_id": route.route_id,
                "stop_sequence": seq,
                "latitude": round(stop.lat, 6),
                "longitude": round(stop.lon, 6),
                "stop_type": "delivery",
                "planned_arrival_time": current.isoformat(),
                "service_time_minutes": stop.service_minutes,
                "planned_start_time": start.isoformat(),
                "vehicle_capacity_units": route.capacity_units,
                "time_window_start": stop.window_start,
                "time_window_end": stop.window_end,
                "demand_units": stop.demand_units,
                "customer_id": stop.customer_id,
            }
        )
        current += timedelta(minutes=stop.service_minutes)
        prev_lat, prev_lon = stop.lat, stop.lon

    return rows


def write_fleet(output: str) -> Path:
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for route in build_fleet():
        rows.extend(_rows_for_route(route))

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="data/samples/v1/sample_fleet.csv",
        help="Where to write the CSV",
    )
    args = parser.parse_args()

    path = write_fleet(args.output)
    routes = build_fleet()
    n_stops = sum(len(r.stops) for r in routes)
    print(f"Wrote {path}: {len(routes)} routes, {n_stops} stops")
    for route in routes:
        print(
            f"  {route.route_id}: {len(route.stops):2d} stops, "
            f"start {route.start_hour:02d}:{route.start_minute:02d}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
