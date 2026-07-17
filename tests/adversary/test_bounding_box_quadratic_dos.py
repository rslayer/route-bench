"""Defect: `validate_csv`'s "large bounding box" check is O(n^2) in the
number of stops on a single route, and it runs synchronously inside the
`POST /sessions` request handler — not offloaded to a thread. A single,
tiny CSV (one route, thousands of closely-clustered stops, well inside the
5,000-stop / 50-route limits) is enough to block the request for multiple
seconds of pure CPU.

Impact: `create_session` in `app/api/routes.py` is an `async def` handler
that calls `validate_csv(tmp_path, analysis_config)` directly, with no
`await asyncio.to_thread(...)`. Because this runs on the single asyncio
event loop, the multi-second CPU burn blocks *every* concurrent request the
process is serving, not just the attacker's own — a small, well-formed
upload becomes a denial of service for the whole worker process. This is
reachable pre-auth: no admin token, no special config, just a CSV where the
depot and stops sit within 500 miles of each other (the everyday case).

Root cause: `_check_bounding_box` in `core/validation.py` does a full
`O(n^2)` nested loop over every pair of stops in a route, computing a
haversine distance for each pair, and only exits early once it *finds* a
pair over 500 miles apart. A single route made entirely of nearby stops
(no such pair exists) runs the loop to completion: ~n^2/2 haversine calls.
"""

from __future__ import annotations

import time
from pathlib import Path

from routebench.core.validation import validate_csv

# One route, 3000 stops, all within a few miles of each other so no early
# exit is possible in _check_bounding_box — this forces the full O(n^2)
# nested loop (~4.5M haversine calls). Measured locally at ~3s; a linear or
# n log n implementation would clear this in well under a second.
_N_STOPS = 3000


def _build_single_route_csv(n_stops: int) -> bytes:
    lines = ["route_id,stop_sequence,latitude,longitude"]
    for s in range(n_stops + 1):
        lat = 32.0 + (s % 1000) * 0.0001
        lon = -96.0 - (s % 1000) * 0.0001
        lines.append(f"R-001,{s},{lat},{lon}")
    return ("\n".join(lines) + "\n").encode()


def test_a_single_dense_route_does_not_block_for_seconds(tmp_path: Path) -> None:
    csv_path = tmp_path / "dense_route.csv"
    csv_path.write_bytes(_build_single_route_csv(_N_STOPS))

    start = time.monotonic()
    fleet, _report = validate_csv(csv_path)
    elapsed = time.monotonic() - start

    assert fleet is not None  # this is a well-formed, in-limits upload
    assert elapsed < 2.0, (
        f"validate_csv took {elapsed:.2f}s for a single {_N_STOPS}-stop route "
        "(well within the 5,000-stop cap) — the O(n^2) bounding-box check "
        "blocks the request handler's event loop for a small, ordinary upload"
    )
