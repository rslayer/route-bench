# RouteBench

Route benchmarking tool that scores planned delivery/service routes across five dimensions, identifies specific inefficiencies with hypothesized causes, and benchmarks against a theoretical optimum.

## Architecture

Four-layer pipeline:

1. **Analysis tools (deterministic)** — Python functions that produce structured `Finding` objects
2. **Analysis orchestrator (agentic)** — Claude-powered loop selecting which analysis tools to run
3. **Report writer (agentic, narrow)** — Claude fills prose slots in a Jinja2 HTML template
4. **Verifier (deterministic)** — Ensures every claim maps to a structured finding

**Firewall principle:** every user-facing claim traces to a deterministic finding. The LLM rephrases and synthesizes; it never invents.

### Hosted Architecture (Phase 8+9)

```
Next.js web  -->  FastAPI API  -->  SessionWorker  -->  Pipeline
                    |                     |
                    v                     v
               Rate Limiter         Async Queue (depth=5)
               Budget Tracker       Timeout Enforcement (600s)
               Admin Endpoints      Storage (local/S3/R2)
               SSE Progress         Telemetry Sink
               Health Checks        Retention Job
```

- **Session-based queueing:** Single-concurrency worker with configurable queue depth
- **Storage abstraction:** Local filesystem or S3-compatible (Cloudflare R2)
- **Progress events:** SSE stream for real-time UI updates
- **Cost tracking:** Per-session token counting with daily budget enforcement
- **Observability:** Sentry integration, structured logging, admin endpoints

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — Python package manager
- Docker & Docker Compose — for OSRM
- An Anthropic API key (for the agent layer)

## Install

```bash
# Clone the repo
git clone https://github.com/rslayer/route-bench.git
cd route-bench

# Install dependencies
uv sync

# Copy and fill in your environment variables
cp .env.example .env
```

## OSRM Setup

RouteBench uses a self-hosted OSRM instance for driving distance/time matrices.

**Without OSRM, RouteBench still runs but withholds the grade.** Travel times fall
back to straight-line estimates with a 1.3 detour factor, every number is labelled
approximate, and the scorecard shows "Withheld" instead of a letter. That is a
deliberate floor, not a substitute — a grade computed on estimated distances would
look authoritative and be wrong.

### One-time data preparation

```bash
scripts/bootstrap_osrm.sh              # Texas, the sample fleet's region
scripts/bootstrap_osrm.sh california
scripts/bootstrap_osrm.sh --url https://download.geofabrik.de/europe/monaco-latest.osm.pbf
```

The script downloads a [Geofabrik](https://download.geofabrik.de/) extract, runs
`osrm-extract` → `osrm-partition` → `osrm-customize`, and writes `OSRM_REGION` to
`.env`. It is resumable: an interrupted download is discarded rather than left
looking complete, and it exits early if the graph is already built.

Budget for it. A US state extract is 0.5–1.5 GB to download, `osrm-extract` wants
roughly as much RAM as the `.pbf` is large, and the whole pipeline takes 5–20
minutes. Use a metro-sized extract from [BBBike](https://extract.bbbike.org/) if
you only need one city.

### Start OSRM

```bash
docker compose up -d osrm
```

Verify — this should return a `durations` matrix, not an error:

```bash
curl -s "http://localhost:5000/table/v1/driving/-96.797,32.777;-96.780,32.800"
```

`docker compose up app` waits for OSRM's healthcheck, because OSRM accepts
connections for several seconds before its graph is queryable. Starting the app
too early means its first matrix call fails and the run quietly degrades to
estimates.

### Sizing note

`--max-table-size` must exceed the largest matrix requested, which is the fleet
benchmark at `(total stops + depots)²`. The default 10000 covers 100 locations.
Exceed it and OSRM returns `TooBig`, which the fallback catches — so the symptom
is a withheld grade rather than an error.

## Run the API Server

```bash
# The API (the frontend lives in web/ — see web/README or `npm --prefix web run dev`)
uv run uvicorn routebench.app.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Or with Docker:

```bash
docker build -t routebench .
docker run -p 8000:8000 --env-file .env routebench
```

To deploy to a hosted site, see [DEPLOY.md](DEPLOY.md).

## Run Tests

```bash
uv run pytest
```

## Lint & Type Check

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/routebench
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Upload CSV, start analysis (returns 202) |
| `GET` | `/sessions/{id}` | Poll session status |
| `GET` | `/sessions/{id}/events` | SSE progress stream |
| `GET` | `/sessions/{id}/report.html` | Download HTML report |
| `GET` | `/sessions/{id}/report.pdf` | Download PDF report |
| `GET` | `/healthz` | Health check |
| `GET` | `/admin/sessions` | List sessions (admin) |
| `GET` | `/admin/costs` | Cost distribution (admin) |
| `POST` | `/admin/sessions/{id}/replay` | Re-render report (admin) |

## Configuration

Key environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required for LLM layer |
| `MATRIX_ENGINE` | `osrm` | `osrm` (free-flow) or `google` (live traffic, billed) |
| `GOOGLE_MAPS_API_KEY` | — | Required when `MATRIX_ENGINE=google` |
| `OSRM_HOST` | `http://localhost:5000` | OSRM endpoint |
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `STORAGE_PATH` | `data/sessions` | Local storage path |
| `R2_ENDPOINT` | — | S3/R2 endpoint URL |
| `R2_BUCKET` | `routebench` | S3 bucket name |
| `MAX_QUEUE_DEPTH` | `5` | Max queued jobs |
| `JOB_TIMEOUT_SECONDS` | `600` | Per-job timeout |
| `DAILY_BUDGET_USD` | `50.0` | Daily spend cap |
| `ADMIN_TOKEN` | — | Admin API auth token |
| `SENTRY_DSN` | — | Sentry error tracking |

### Traffic profiles

OSRM returns **free-flow** travel times — static road attributes, no time-of-day
variation. A traffic profile scales those times by a speed factor per time band,
so time-window compliance and shift overruns are graded on a realistic clock.
This is *not* live or historical traffic data.

Pass a profile per upload in the `config` JSON of `POST /sessions`. Use the
shipped `urban_us` profile (0.75× speed 07:00–09:00, 0.80× 16:00–18:30):

```json
{ "traffic": "urban_us" }
```

Or define bands inline (`start` inclusive, `end` exclusive, local wall-clock;
`speed_factor` below 1.0 slows travel):

```json
{
  "traffic": {
    "bands": [
      { "start": "07:00", "end": "09:00", "speed_factor": 0.75 },
      { "start": "16:00", "end": "18:30", "speed_factor": 0.80 }
    ],
    "default_factor": 1.0
  }
}
```

Omitting `traffic` keeps free-flow behavior, and the report says so. Notes:

- **Distances are never changed** — only durations. Distance metrics and the
  sequencing index are identical with and without a profile.
- **Band assignment is a single-pass approximation.** Each leg is banded by its
  origin's departure time, estimated from the plan's free-flow schedule and not
  iterated to a fixed point. The methodology page discloses this.
- **Timestamps are read as depot-local wall clock**, consistent with how time
  windows are already interpreted. Uploading UTC timestamps for a depot in
  another timezone will band the wrong hours.

## Deployment

Three Fly.io apps: the API (`fly.toml`), the OSRM sidecar with its graph baked
in (`fly.osrm.toml`), and the Next.js frontend (`fly.web.toml`). Sessions persist
to Cloudflare R2. The full runbook — secrets, order, verification — is in
[DEPLOY.md](DEPLOY.md).

## Scripts

- `scripts/generate_synthetic.py` — Generate synthetic test CSVs
- `scripts/run_local.py` — Headless pipeline runner (no API server needed)
- `scripts/benchmark_costs.py` — Grid benchmark across fleet sizes
- `scripts/load_test.py` — Concurrent upload stress test

## Project Structure

```
route-bench/
├── src/routebench/
│   ├── core/           # Schemas, validation, config, exceptions
│   ├── infra/          # Matrix providers, storage backends, telemetry
│   ├── analysis/       # Scoring, diagnosis, benchmark, visuals
│   ├── report/         # Jinja2 templates, prose slots, PDF
│   ├── agent/          # Orchestrator, writer, verifier, prompts
│   └── app/            # FastAPI, pipeline, worker, sessions
│       └── api/        # Routes, admin, app factory
├── tests/              # 151 tests
├── data/
├── scripts/
└── notebooks/
```

## License

RouteBench is **Fair Source** software, licensed under the [Functional Source License, Version 1.1, ALv2 Future License (FSL-1.1-ALv2)](LICENSE.md).

In plain terms:

- **You may** read, run, copy, modify, and redistribute this code for any Permitted Purpose — including internal use, education, research, auditing our methodology, and building non-competing products or services.
- **You may not** offer RouteBench, or a substitute for it, as a commercial hosted service that competes with RouteBench.
- **Every release converts to Apache 2.0 two years after its publication date**, at which point that version is fully open source with no restrictions.

We keep the methodology public on purpose: a route benchmarking referee should be auditable. If you believe our scoring, diagnostics, or solver comparison is wrong, the code is right here — open an issue.

This license is not an OSI-approved open source license during its first two years. If your use case is blocked by the competing-use restriction, contact the author.
