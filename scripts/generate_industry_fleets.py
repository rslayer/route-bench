#!/usr/bin/env python3
"""Generate one demo fleet per industry profile.

A representative, ready-to-upload sample for each vertical, so a courier operator
sees the dense-drop shape and a big-and-bulky operator sees the few-stops /
long-service / appointment-window shape. Service times come straight from
core/industry.py, so the demos stay in sync with the profiles and sit inside each
profile's plausible band (no false data-quality findings).

These are demo-scale, not real-scale: a real courier route is 150-200 stops, but
that is an expensive matrix and a slow solve, so the samples are smaller while
keeping each vertical's density, service time, and constraints characteristic.

Deterministic (no RNG); regenerating is byte-identical.

    uv run python scripts/generate_industry_fleets.py
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from routebench.core.industry import INDUSTRY_PROFILES

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "samples" / "industry"
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
    "time_window_start",
    "time_window_end",
    "demand_units",
    "customer_id",
]


@dataclass(frozen=True)
class DemoSpec:
    profile_key: str  # ties service time + label to core/industry.py
    metro: str
    center: tuple[float, float]
    radius_deg: float  # tight for dense verticals, wide for sparse ones
    n_routes: int
    stops_per_route: int
    start_time: str  # "HH:MM"
    date: str  # "YYYY-MM-DD"
    time_windows: bool  # appointment windows (big & bulky, merchandising)


# One demo per profile. Densities and windows are chosen to look like the
# vertical; stop counts are demo-scale (see module docstring).
DEMOS: tuple[DemoSpec, ...] = (
    DemoSpec(
        "courier", "New York City", (40.7480, -73.9855), 0.030, 4, 25, "08:00", "2025-06-10", False
    ),
    DemoSpec(
        "big_bulky", "Phoenix, AZ", (33.4780, -112.0740), 0.170, 3, 7, "08:00", "2025-06-11", True
    ),
    DemoSpec(
        "dsd_quickdrop",
        "Chicago, IL",
        (41.8800, -87.6300),
        0.080,
        4,
        20,
        "06:00",
        "2025-06-12",
        False,
    ),
    DemoSpec(
        "dsd_merchandising",
        "Dallas, TX",
        (32.7800, -96.8000),
        0.100,
        3,
        12,
        "06:30",
        "2025-06-13",
        True,
    ),
)


def _scatter(
    center: tuple[float, float], n: int, radius: float, seed: float
) -> list[tuple[float, float]]:
    clat, clon = center
    lon_scale = math.cos(math.radians(clat))
    out: list[tuple[float, float]] = []
    for i in range(n):
        angle = i * _GOLDEN + seed
        r = radius * math.sqrt((i + 0.5) / n)
        lat = clat + r * math.sin(angle)
        lon = clon + (r * math.cos(angle)) / lon_scale
        out.append((round(lat, 5), round(lon, 5)))
    return out


def _windows(start: str, seq: int) -> tuple[str, str]:
    """Staggered 4-hour appointment windows, the big & bulky standard. Spaced so
    the planned order mostly lands inside them — a clean demo, not a trap."""
    base = datetime.combine(datetime(2000, 1, 1), datetime.strptime(start, "%H:%M").time())
    open_t = base + timedelta(minutes=90 * (seq - 1))
    return open_t.strftime("%H:%M"), (open_t + timedelta(hours=4)).strftime("%H:%M")


def build_rows(spec: DemoSpec) -> list[dict[str, object]]:
    profile = INDUSTRY_PROFILES[spec.profile_key]
    service = profile.default_service_minutes
    start_iso = f"{spec.date}T{spec.start_time}:00+00:00"
    prefix = spec.profile_key[:3]
    rows: list[dict[str, object]] = []
    for route_idx in range(spec.n_routes):
        route_id = f"R{route_idx + 1:03d}"
        # Fan each route into its own sector around the shared depot.
        sector = route_idx * (2 * math.pi / spec.n_routes)
        lon_scale = math.cos(math.radians(spec.center[0]))
        cluster = (
            spec.center[0] + 0.4 * spec.radius_deg * math.sin(sector),
            spec.center[1] + 0.4 * spec.radius_deg * math.cos(sector) / lon_scale,
        )
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
                "time_window_start": "",
                "time_window_end": "",
                "demand_units": 0,
                "customer_id": "",
            }
        )
        stops = _scatter(cluster, spec.stops_per_route, spec.radius_deg * 0.6, seed=route_idx * 1.7)
        for seq, (lat, lon) in enumerate(stops, start=1):
            tw_start, tw_end = _windows(spec.start_time, seq) if spec.time_windows else ("", "")
            rows.append(
                {
                    "route_id": route_id,
                    "stop_sequence": seq,
                    "latitude": lat,
                    "longitude": lon,
                    "stop_type": "delivery",
                    "planned_arrival_time": "",
                    "service_time_minutes": service,
                    "planned_start_time": start_iso,
                    "time_window_start": tw_start,
                    "time_window_end": tw_end,
                    "demand_units": 1,
                    "customer_id": f"{prefix}-{route_id}-{seq:02d}",
                }
            )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for spec in DEMOS:
        rows = build_rows(spec)
        path = OUT_DIR / f"{spec.profile_key}.csv"
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_HEADER)
            writer.writeheader()
            writer.writerows(rows)
        n_stops = spec.n_routes * spec.stops_per_route
        svc = INDUSTRY_PROFILES[spec.profile_key].default_service_minutes
        print(
            f"{path.name}: {spec.n_routes} routes, {n_stops} stops, "
            f"{svc:g} min service — {spec.metro}"
        )


if __name__ == "__main__":
    main()
