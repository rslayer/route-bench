# RouteBench — Phase 8 & 9 (Hosted)

This spec retargets Phases 8 and 9 from the original local-first build (see `SPEC_2.md`) for a hosted deployment. It preserves the firewall architecture from Phases 0–7 — all changes live in `app/` and `infra/`.

## Hosting decisions (lock these first)

- **Compute:** single container, deploy to **Fly.io** or **Google Cloud Run** (both support always-on workers and persistent disk/volumes; either handles WeasyPrint + OR-Tools without surgery). The spec below assumes Fly.io; swap target with no code changes.
- **Frontend:** Streamlit stays — served from the same container on `:8501`, fronted by the FastAPI app on `:8000`. No separate SPA in this phase. If/when you want a marketing-grade frontend, Phase 10 can add a Next.js shell that calls the same FastAPI endpoints.
- **Object storage:** S3-compatible. Default to **Cloudflare R2** (zero egress fees, S3 API). Sessions persist there, not on disk.
- **OSRM:** runs as a **second Fly app** (`routebench-osrm`) on a dedicated machine with the regional `.osm.pbf` pre-extracted into a Fly volume. The main app reaches it over the private Fly network at `http://routebench-osrm.internal:5000`. One region per app; document how to add more.
- **Secrets:** `fly secrets` (or Cloud Run env). No `.env` in production. `Settings` keeps reading from env — no code change.
- **Job model:** report generation is async. The HTTP handler enqueues a job and returns a `session_id`; a background worker (in-process `asyncio` task in this phase, see Phase 9 for an upgrade path) runs the pipeline; the client polls `/sessions/{id}` for status.
- **Concurrency cap:** one running session per machine. Excess requests are queued in-memory with a hard ceiling (reject with 429 above it). Right-size on Fly by scaling machine count, not threads.

The architectural firewall is unchanged: `core/`, `analysis/`, `report/`, `agent/` are untouched. Only `app/` and `infra/` grow.

---

## Phase 8 — Hosted pipeline + API + minimal UI

**Goal:** A user visits the deployed URL, uploads a CSV, watches progress, and downloads a report. No auth, no payments yet.

### Tasks

1. **`app/pipeline.py`** — `async def run_session(upload_path, config, deps) -> SessionResult`. `deps` is a small dataclass carrying the injected `MatrixProvider`, `StorageBackend`, `LLMClient`, and `Telemetry`. Pipeline stages exactly as in the original spec (validate → orchestrate → write → verify → render → persist). Each stage emits a structured progress event via an `asyncio.Queue` passed in through `deps`. Catch `RouteBenchError` subclasses at the boundary; let everything else bubble (the worker turns it into a `failed` session).

2. **`infra/storage/s3.py`** — `S3StorageBackend` implementing `StorageBackend`. Uses `aioboto3` with R2 credentials. Layout in the bucket: `sessions/{session_id}/{upload.csv,report.html,report.pdf,analysis.json,telemetry.json,status.json}`. `status.json` is the single source of truth for session state (see schema below). Generates pre-signed GET URLs (15-min TTL) for the report download endpoint.

3. **`app/sessions.py`** — `SessionRegistry`: an in-process dict of `session_id -> SessionState` for jobs in flight, plus a thin layer that reads/writes `status.json` in storage for completed jobs. Schema:

   ```python
   class SessionStatus(BaseModel):
       session_id: str
       state: Literal["queued", "validating", "analyzing", "writing", "rendering", "succeeded", "failed"]
       progress_pct: int
       stage_detail: str
       created_at: datetime
       updated_at: datetime
       error: SessionError | None
       artifacts: SessionArtifacts | None   # populated on success
       cost: CostSummary | None
   ```

4. **`app/worker.py`** — `SessionWorker`: one `asyncio.Task` per machine, pulls from an `asyncio.Queue`, runs `pipeline.run_session`, writes `status.json` after each stage. Configurable `MAX_QUEUE_DEPTH` (default 5) and `JOB_TIMEOUT_SECONDS` (default 600). On timeout, mark `failed` with a `JobTimeoutError`.

5. **`app/api/`** — FastAPI app. Endpoints:
   - `POST /sessions` — multipart upload (`file`, `config` JSON). Validates the CSV synchronously (cheap; rejects malformed input before queueing). On success, writes `upload.csv` to storage, enqueues the job, returns `{session_id, status_url}`. On validation failure, returns 422 with the `ValidationReport`.
   - `GET /sessions/{id}` — returns `SessionStatus`. Used for polling.
   - `GET /sessions/{id}/events` — SSE stream of progress events. Same payload as `status.json` plus stage transitions. Closes on terminal state.
   - `GET /sessions/{id}/report.html` — 302 to a pre-signed URL.
   - `GET /sessions/{id}/report.pdf` — same.
   - `GET /healthz` — readiness probe; checks OSRM reachable and storage writable.

   No CORS in this phase (same-origin only, since Streamlit is co-hosted).

6. **`app/streamlit_app.py`** — minimal UI that talks to the FastAPI app over `http://localhost:8000` (in-process). File uploader → POST → poll SSE → render the HTML report in an `iframe` with a download bar. Configuration form mirrors `AnalysisConfig`. No need to duplicate validation; surface whatever the API returns.

7. **`Dockerfile`** — multi-stage:
   - Builder: `python:3.12-slim`, install `uv`, `uv sync --frozen`.
   - Runtime: same base, copy venv, install WeasyPrint system deps (`libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf2.0-0 libffi8 shared-mime-info fonts-dejavu`), copy source, expose 8000 and 8501. Entrypoint runs `supervisord` (or `honcho`) starting FastAPI (uvicorn) and Streamlit together.

8. **`fly.toml`** (main app) and **`fly.osrm.toml`** (OSRM sidecar app). Document in README.md how to:
   - `flyctl apps create routebench-osrm`, attach a volume, run `osrm-extract`/`osrm-contract` once in a one-shot machine.
   - `flyctl secrets set ANTHROPIC_API_KEY=… R2_ACCESS_KEY_ID=… R2_SECRET_ACCESS_KEY=… R2_BUCKET=… R2_ENDPOINT=… CLAUDE_MODEL=…`.
   - `flyctl deploy` for the main app.

9. **`scripts/run_local.py`** — unchanged purpose, but uses the same `pipeline.run_session` against a `LocalStorageBackend`. Kept for headless smoke testing.

10. **Configuration additions** in `core/config.py`:
    - `STORAGE_BACKEND: Literal["local", "s3"] = "local"`
    - `R2_*` settings (endpoint, bucket, keys, region)
    - `OSRM_HOST` defaults to `http://localhost:5000` locally, set to the Fly internal URL in prod via secret
    - `MAX_QUEUE_DEPTH`, `JOB_TIMEOUT_SECONDS`, `SESSION_TTL_HOURS` (for retention)

11. **Tests:**
    - `tests/app/test_api.py` — FastAPI `TestClient`, mocked storage and pipeline. Cover: upload happy path → 202 with session_id; malformed CSV → 422 with `ValidationReport`; polling lifecycle; queue-full → 429.
    - `tests/app/test_worker.py` — drives the worker with a stub pipeline; asserts status transitions and timeout behavior.
    - `tests/infra/test_s3_storage.py` — uses **moto** (mock S3) to verify reads/writes and pre-signed URL shape.
    - One real E2E test with a mocked LLM that runs the full FastAPI stack on a synthetic CSV.

### Acceptance criteria

- `docker compose up` runs the full stack locally (FastAPI + Streamlit + local OSRM + a moto S3 stub). Uploading a synthetic CSV produces a downloadable report.
- `flyctl deploy` to a fresh Fly org reaches a green `/healthz`.
- A 30-route synthetic fleet, uploaded through the deployed UI, returns a complete HTML report within the documented runtime budget.
- `mypy --strict` and `ruff check` continue to pass over the expanded `app/` and `infra/`.

### Do not

- Do not add auth, Stripe, user accounts, or DBs. (Phase 10.)
- Do not introduce a separate frontend framework.
- Do not move the queue to Redis/SQS yet — single-machine `asyncio.Queue` is intentional for this phase.

---

## Phase 9 — Observability, cost telemetry, sample report, hardening

**Goal:** Confidently leave the deployed app running. Measure real per-session cost. Publish a polished sample report.

### Tasks

1. **`app/telemetry_sink.py`** — replaces the local `telemetry.flush_to_disk` with a sink that also writes to storage (`sessions/{id}/telemetry.json`) and emits aggregate counters to **Logfire** (or OpenTelemetry → any compatible backend). Track:
   - LLM tokens (in/out) per slot, per session, per model
   - Matrix calls (cached vs uncached, n cells)
   - Solver calls (problem type, n stops, wall time, optimality gap)
   - Pipeline stage durations
   - Per-session computed dollar cost (Claude tokens × posted price; OSRM is free; storage/compute amortized)

2. **`app/api/admin.py`** — gated by a shared `ADMIN_TOKEN` header. Endpoints:
   - `GET /admin/sessions?since=…` — paginated session list with cost summary
   - `GET /admin/costs?window=…` — aggregated cost-per-session distribution (p50/p95/max)
   - `POST /admin/sessions/{id}/replay` — re-renders a stored `analysis.json` without re-running the pipeline. Useful for template changes.

3. **`scripts/benchmark_costs.py`** — runs the full hosted pipeline (against a staging deployment, via the API) on a grid of synthetic fleets: `n_routes ∈ {5, 15, 30, 50} × density ∈ {sparse, normal, dense}`. Writes a CSV summarizing wall time, token counts, dollar cost, finding counts. Acceptance: completes the grid in <30 minutes and writes a report.

4. **`data/samples/`** — produce one **hand-curated** synthetic fleet that exercises every diagnosis category (a tight sequencing offender, a time-window-bound idle case, an outlier stop, two overlapping territories, dispatch clustering, a real benchmark gap). Run it through the live pipeline; commit the resulting `report.html` and `report.pdf`. Host them at `https://<deploy>/samples/v1/report.html` as static files served by FastAPI.

5. **Cost guardrails:**
   - Per-session hard cap on input tokens (estimated pre-call). If a session would exceed it, fail fast with `BudgetExceededError`.
   - Daily total-spend cap, read from `Settings.DAILY_BUDGET_USD`. Once exceeded, `POST /sessions` returns 503 with a clear message until UTC midnight.
   - Telemetry counter for budget rejections.

6. **Retention job:** background task that runs hourly, deletes session artifacts older than `SESSION_TTL_HOURS` (default 72) from R2. Keeps `telemetry.json` for 30 days for cost analytics.

7. **Rate limiting** at the API edge: per-IP, 10 sessions/hour, 100/day. Use **slowapi**. Log rejections.

8. **Error reporting:** wire **Sentry** for unhandled exceptions in worker + API. Scrub uploaded CSVs from breadcrumbs.

9. **README.md** update:
   - Architecture diagram (one image)
   - Deployment guide (Fly app + OSRM sidecar + R2 bucket setup)
   - Cost numbers for the four fleet sizes from `benchmark_costs.py`
   - Link to the live sample report
   - Local-dev guide (unchanged; `uv run` + `docker compose`)

10. **Load test:** `scripts/load_test.py` using `httpx` to fire 20 concurrent uploads at staging. Asserts queue behavior (no 5xx, only 429s when full) and that p95 completion time stays under target.

### Acceptance criteria

- Cost dashboard shows real per-session cost within ±10% of the posted Claude price.
- `benchmark_costs.py` produces a reproducible CSV; the README quotes its p50 numbers.
- Sample report is linked from the README and renders identically in Chrome/Safari/Firefox.
- Load test of 20 concurrent requests against a single machine produces zero unhandled exceptions; excess requests get 429.
- Sentry receives no errors during a clean end-to-end run.
- Daily budget cap demonstrably trips when forced low in staging.

### Do not

- Do not introduce a real DB. Storage is sufficient for sessions; telemetry aggregates live in the observability backend.
- Do not implement Stripe or accounts. The `$1-per-session` business model belongs in Phase 10.
- Do not add multi-region orchestration. One OSRM region per deployment; document the swap procedure.

---

## What's deferred to Phase 10 (so it doesn't sneak into 8/9)

- Stripe checkout + `$1` paywall
- User accounts, history page, saved configs
- Multi-region OSRM and routing-by-geography
- Postgres (only when accounts arrive)
- A real frontend (Next.js + the existing FastAPI API)
- Webhook callbacks on session completion
