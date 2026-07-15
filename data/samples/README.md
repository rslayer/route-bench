# Sample Reports

A hand-curated fleet and the reports generated from it.

## The sample fleet

`v1/sample_fleet.csv` comes from `scripts/generate_sample_fleet.py`. Unlike
`scripts/generate_synthetic.py`, which places stops at random, every route here is
positioned to exercise one specific analysis — so the report demonstrates each one
rather than whatever a random draw happens to trip:

| Route | Stops | Start | Exists to demonstrate |
|-------|-------|-------|-----------------------|
| R001 | 8 | 07:30 | **Sequencing** — zigzags east-west instead of sweeping the corridor. Also supplies the per-route benchmark gap. |
| R002 | 6 | 07:30 | **Time pressure** — dispatched at 07:30 for stops that do not open until 11:00. |
| R003 | 7 | 07:35 | **Outlier** — a tight Oak Cliff cluster plus one stop stranded 14 miles east. Also under-utilised. |
| R004 | 4 | 07:30 | **Territory** — interleaves with R005 over the same North Dallas ground. |
| R005 | 4 | 07:40 | **Territory** — the other half of that overlap; also gives the fleet benchmark stops worth migrating. |
| R006 | 7 | 15:00 | **Compliance** — more afternoon work than its 16:00 windows allow, running into the peak band. |

**Dispatch clustering** falls out of the start times: five of six routes leave
within 15 minutes of each other, above the tool's 70% threshold.

Coordinates are real Dallas locations so a Texas OSRM extract can route them. The
generator is deterministic (no RNG), so regenerating produces byte-identical CSV
and the sample report stays reproducible.

```bash
uv run python scripts/generate_sample_fleet.py
```

`tests/analysis/test_sample_fleet.py` asserts every category still fires, so the
sample cannot silently decay into a random fleet when someone moves a stop.

## Generating the reports

The committed reports must come from the **live** pipeline — real OSRM road
matrices and real LLM-written prose. Do not commit output produced with a stubbed
matrix or a mocked LLM: it would look like genuine output while being nothing of
the kind.

Prerequisites:

1. **Anthropic API key** in `.env` (never on the command line):
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
2. **OSRM** with a Texas extract — see the OSRM Setup section of the root README:
   ```bash
   docker compose up osrm
   ```
3. **WeasyPrint system libraries**, only needed for the PDF. On macOS:
   ```bash
   brew install pango cairo gdk-pixbuf libffi
   ```

Then:

```bash
uv run python scripts/generate_sample_fleet.py

# Free-flow baseline
uv run python scripts/run_local.py data/samples/v1/sample_fleet.csv

# Traffic-adjusted — see "Traffic profiles" in the root README
uv run python scripts/run_local.py data/samples/v1/sample_fleet.csv \
    --config '{"traffic": "urban_us", "include_pdf": true}'
```

Each run prints its session id and writes to `output/<session-id>/`:

```bash
cp output/<session-id>/report.html v1/report.html
cp output/<session-id>/report.pdf  v1/report.pdf
```

Publishing both variants is worthwhile: the same fleet under free-flow and under
`urban_us` shows the profile tightening time-window feasibility while leaving
distances untouched, which is the clearest demonstration of what it does.

Once committed, these are served at `/samples/v1/report.html` by the FastAPI app.

## Status

`v1/` holds the curated fleet only. The rendered `report.html` and `report.pdf` are
**not committed yet** — they need the live prerequisites above.
