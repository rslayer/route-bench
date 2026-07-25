# Test fleets

A diverse set of route-plan CSVs for exercising RouteBench against varied
geography, size, density, and constraints — not the single hand-tuned fleet the
committed sample report is built from (that lives at `../v1/sample_fleet.csv`).

Regenerate deterministically (no RNG, byte-identical output):

```bash
uv run python scripts/generate_test_fleets.py
```

These are **illustrative fixtures**, not audited real delivery data. Coordinates
are scattered within each metro's road network so the Google matrix/geometry
engine snaps them to real streets; they do not correspond to real addresses or
customers.

| File | Metro | Routes | Stops | Exists to exercise |
|------|-------|--------|-------|--------------------|
| `01_urban_dense_nyc.csv` | New York City | 4 | 51 | Dense urban grid, many stops/route, clustered dispatch (dispatch-clustering finding). |
| `02_rural_sparse_montana.csv` | Bozeman, MT | 3 | 15 | Sparse rural fleet, long inter-stop legs, low density, big matrix distances. |
| `03_overcapacity_chicago.csv` | Chicago | 5 | 42 | Demand above vehicle capacity — over-utilization and fleet rebalancing. |
| `04_timewindows_la.csv` | Los Angeles | 4 | 28 | Tight per-stop time windows (some infeasible), pickup/delivery mix, afternoon peak. |
| `05_large_route_atlanta.csv` | Atlanta | 2 | 36 | One 28-stop route (Google geometry chunking + long-route sequencing) beside a small one. |

## Schema

All files use the canonical columns the upload endpoint accepts. Required:
`route_id, stop_sequence, latitude, longitude`. Everything else is optional;
`stop_sequence` 0 marks the depot. See `web/src/lib/schema.ts` and
`src/routebench/core/validation.py` for the full contract.

Every file validates with zero warnings. To confirm after regenerating:

```bash
uv run python -c "
from pathlib import Path
from routebench.core.validation import validate_csv
from routebench.core.config import AnalysisConfig
for f in sorted(Path('data/samples/test_fleets').glob('*.csv')):
    fleet, report = validate_csv(f, AnalysisConfig())
    print(f.name, len(fleet.routes), 'routes', sum(len(r.stops) for r in fleet.routes), 'stops')
"
```
