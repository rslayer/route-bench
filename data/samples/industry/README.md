# Industry demo fleets

One ready-to-upload sample per industry profile, generated from
`core/industry.py` so service times stay in sync with each profile (and sit
inside its plausible band). Pair each with its matching **industry** in the
upload panel to see the vertical-tuned grade.

Regenerate deterministically:

```bash
uv run python scripts/generate_industry_fleets.py
```

| File | Profile | Metro | Routes / Stops | Service | Shows |
|------|---------|-------|----------------|---------|-------|
| `courier.csv` | Courier / parcel | New York City | 4 / 100 | 2 min | Dense drops; sequencing & density weighting |
| `big_bulky.csv` | Big & bulky | Phoenix | 3 / 21 | 90 min | Few stops, long service, 4-hr appointment windows; compliance weighting |
| `dsd_quickdrop.csv` | F&B DSD quick-drop | Chicago | 4 / 80 | 18 min | Fast retail drops; territory/fleet weighting |
| `dsd_merchandising.csv` | F&B large-format | Dallas | 3 / 36 | 40 min | Merchandising stops with scheduled windows |

These are **demo-scale**, not real-scale (a real courier route is 150-200 stops;
that is an expensive matrix and slow solve). Densities, service times, and
constraints are kept characteristic of each vertical. First benchmark-on run of
a fleet pays for its matrix; re-runs are cached. Untick the benchmark for a fast,
cheap look.
