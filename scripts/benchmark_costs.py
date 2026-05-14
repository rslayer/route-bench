"""Benchmark costs — runs pipeline on a grid of synthetic fleets via the API.

Usage:
    uv run python scripts/benchmark_costs.py --api-url https://routebench.fly.dev
    uv run python scripts/benchmark_costs.py --api-url http://localhost:8000

Produces a CSV with columns: n_routes, density, wall_time_s, input_tokens,
output_tokens, cost_usd, finding_count.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def generate_grid_csv(n_routes: int, density: str) -> bytes:
    """Generate synthetic CSV data for benchmarking."""
    from io import StringIO

    rows = []
    base_lat, base_lon = 32.78, -96.80

    stops_per_route = {"sparse": 5, "normal": 15, "dense": 30}
    n_stops = stops_per_route.get(density, 15)

    for r in range(n_routes):
        route_id = f"R-{r:03d}"
        for s in range(n_stops):
            rows.append(
                {
                    "route_id": route_id,
                    "stop_sequence": s,
                    "latitude": round(base_lat + (s * 0.01) + (r * 0.05), 6),
                    "longitude": round(base_lon + (s * 0.01) - (r * 0.05), 6),
                    "planned_arrival": f"2024-01-15T{8 + s // 4:02d}:{(s * 15) % 60:02d}:00",
                    "planned_departure": f"2024-01-15T{8 + s // 4:02d}:{(s * 15) % 60 + 5:02d}:00",
                    "service_minutes": 5,
                    "units": 1,
                }
            )

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def run_benchmark(
    api_url: str, n_routes: int, density: str, timeout: int = 600
) -> dict[str, object]:
    """Run a single benchmark and return metrics."""
    csv_data = generate_grid_csv(n_routes, density)

    start = time.monotonic()
    resp = httpx.post(
        f"{api_url}/sessions",
        files={"file": ("benchmark.csv", csv_data, "text/csv")},
        timeout=30.0,
    )

    if resp.status_code != 202:
        return {
            "n_routes": n_routes,
            "density": density,
            "error": f"Upload failed: {resp.status_code}",
        }

    session_id = resp.json()["session_id"]

    # Poll until complete
    while time.monotonic() - start < timeout:
        status_resp = httpx.get(f"{api_url}/sessions/{session_id}", timeout=10.0)
        status = status_resp.json()

        if status["state"] == "succeeded":
            wall_time = time.monotonic() - start
            cost = status.get("cost", {})
            return {
                "n_routes": n_routes,
                "density": density,
                "wall_time_s": round(wall_time, 1),
                "input_tokens": cost.get("input_tokens", 0),
                "output_tokens": cost.get("output_tokens", 0),
                "cost_usd": cost.get("total_cost_usd", 0.0),
                "finding_count": 0,  # Would need analysis.json
            }
        elif status["state"] == "failed":
            wall_time = time.monotonic() - start
            error = status.get("error", {})
            return {
                "n_routes": n_routes,
                "density": density,
                "wall_time_s": round(wall_time, 1),
                "error": error.get("message", "Unknown"),
            }

        time.sleep(2.0)

    return {
        "n_routes": n_routes,
        "density": density,
        "error": "Timeout",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark RouteBench costs")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--output", default="benchmark_results.csv", help="Output CSV path")
    args = parser.parse_args()

    grid = [(n, d) for n in [5, 15, 30, 50] for d in ["sparse", "normal", "dense"]]

    results = []
    for n_routes, density in grid:
        print(f"Running: {n_routes} routes, {density} density...")
        result = run_benchmark(args.api_url, n_routes, density)
        results.append(result)
        print(f"  -> {result}")

    # Write CSV
    if results:
        fieldnames = list(results[0].keys())
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
