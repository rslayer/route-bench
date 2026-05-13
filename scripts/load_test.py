"""Load test — fires concurrent uploads at the API to verify queue behavior.

Usage:
    uv run python scripts/load_test.py --api-url http://localhost:8000 --concurrency 20
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from io import StringIO
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_csv(n_routes: int = 3) -> bytes:
    """Generate a small synthetic CSV."""
    rows = []
    base_lat, base_lon = 32.78, -96.80

    for r in range(n_routes):
        route_id = f"R-{r:03d}"
        for s in range(10):
            rows.append(
                {
                    "route_id": route_id,
                    "stop_sequence": s,
                    "latitude": round(base_lat + s * 0.01 + r * 0.05, 6),
                    "longitude": round(base_lon + s * 0.01 - r * 0.05, 6),
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


async def _upload_one(
    client: httpx.AsyncClient, api_url: str, csv_data: bytes, idx: int
) -> dict[str, object]:
    """Upload one CSV and return the result."""
    start = time.monotonic()
    try:
        resp = await client.post(
            f"{api_url}/sessions",
            files={"file": (f"test_{idx}.csv", csv_data, "text/csv")},
            timeout=30.0,
        )
        elapsed = time.monotonic() - start
        return {
            "index": idx,
            "status_code": resp.status_code,
            "elapsed_s": round(elapsed, 2),
            "body": resp.json() if resp.status_code < 500 else resp.text,
        }
    except Exception as exc:
        elapsed = time.monotonic() - start
        return {
            "index": idx,
            "status_code": 0,
            "elapsed_s": round(elapsed, 2),
            "error": str(exc),
        }


async def run_load_test(api_url: str, concurrency: int) -> None:
    """Fire concurrent uploads and report results."""
    csv_data = _make_csv(n_routes=3)

    print(f"Sending {concurrency} concurrent uploads to {api_url}...")

    async with httpx.AsyncClient() as client:
        tasks = [_upload_one(client, api_url, csv_data, i) for i in range(concurrency)]
        results = await asyncio.gather(*tasks)

    # Analyze results
    status_counts: dict[int, int] = {}
    for r in results:
        code = int(str(r["status_code"]))
        status_counts[code] = status_counts.get(code, 0) + 1

    print(f"\nResults ({concurrency} requests):")
    for code, count in sorted(status_counts.items()):
        print(f"  HTTP {code}: {count}")

    # Check assertions
    n_5xx = sum(v for k, v in status_counts.items() if 500 <= k < 600)
    n_429 = status_counts.get(429, 0)
    n_202 = status_counts.get(202, 0)

    print(f"\n  Accepted (202):     {n_202}")
    print(f"  Rate limited (429): {n_429}")
    print(f"  Server errors (5xx): {n_5xx}")

    if n_5xx > 0:
        print("\n  FAIL: Got 5xx errors — unhandled exceptions detected!")
        sys.exit(1)
    else:
        print("\n  PASS: No 5xx errors. Queue overflow handled gracefully.")


def main() -> None:
    parser = argparse.ArgumentParser(description="RouteBench load test")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--concurrency", type=int, default=20, help="Number of concurrent uploads")
    args = parser.parse_args()

    asyncio.run(run_load_test(args.api_url, args.concurrency))


if __name__ == "__main__":
    main()
