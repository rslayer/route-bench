# Deploying RouteBench to Fly.io

RouteBench deploys as Fly apps:

| App | What | Config | Needed? |
| --- | --- | --- | --- |
| `routebench` | FastAPI API | `fly.toml` | always |
| `routebench-web` | Next.js frontend | `fly.web.toml` | always |
| `routebench-osrm` | Self-hosted OSRM routing engine | `fly.osrm.toml` | only if `MATRIX_ENGINE=osrm` |

**The default deploy runs on the Google matrix engine** (`MATRIX_ENGINE=google`
in `fly.toml`), which gives continent-wide coverage and live traffic without
self-hosting a routing graph. So the OSRM sidecar is **optional** — skip step 1
below unless you switch to the self-hosted engine. The trade-off is cost: Google
bills per element (see the cost model further down), so watch your volume.

Session artifacts live in **Cloudflare R2** (or any S3-compatible store), not on
the machine — Fly disks are ephemeral, and a redeploy would otherwise wipe every
report. Only the matrix cache sits on a small Fly volume.

This is a real deploy: it needs a Fly account, an R2 bucket, a Google Maps API
key, and it costs money. None of it is automated — run the steps below by hand
the first time, in order.

---

## 0. One-time prerequisites

- `fly` CLI installed and `fly auth login` done.
- A Cloudflare R2 bucket (or S3/MinIO). Note its **endpoint URL**, **access key
  id**, **secret access key**, and **bucket name**.
- A **Google Maps API key** with the **Routes API** enabled and billing on
  (required for the default engine). Understand the cost first — see
  "The Google engine cost model" below.
- Optionally an Anthropic API key. Without it the analysis still runs — findings
  are filled from templates instead of written prose — so you can ship first and
  add it later.

Create the apps (no deploy yet). Skip `routebench-osrm` unless you plan to run
the self-hosted engine:

```bash
fly apps create routebench
fly apps create routebench-web
fly apps create routebench-osrm   # only for MATRIX_ENGINE=osrm
```

---

## 1. OSRM (OPTIONAL — skip on the default Google engine)

**Only needed if you set `MATRIX_ENGINE=osrm`.** On the default Google engine
there is no self-hosted routing graph, so skip straight to step 2.

The graph is built into the image, so this is one command. It is the slowest
step: it downloads a regional extract (a US state is ~1 GB) and runs the full
extract/partition/customize pipeline in the builder.

```bash
fly deploy -c fly.osrm.toml
```

The default region is Texas, which covers the bundled Dallas sample. For a
different region, or a smaller/faster build, override the extract URL:

```bash
# A whole country/state from Geofabrik:
fly deploy -c fly.osrm.toml \
  --build-arg OSRM_PBF_URL=https://download.geofabrik.de/europe/monaco-latest.osm.pbf

# A single metro (much smaller, much faster) from https://extract.bbbike.org
```

If the build runs out of memory, use a larger remote builder
(`fly deploy -c fly.osrm.toml --vm-memory 4096`) — `osrm-extract` needs RAM
roughly equal to the `.pbf` size.

Verify it is serving its graph (not just up):

```bash
fly ssh console -a routebench-osrm -C \
  "wget -qO- 'http://localhost:5000/nearest/v1/driving/-96.797,32.777'"
# expect JSON containing "waypoints"
```

---

## 2. API

Set the secrets **before** the first deploy. `fly.toml` already sets
`STORAGE_BACKEND=s3` and `MATRIX_ENGINE=google`; these are the values it cannot
ship in plaintext. On the Google engine, `GOOGLE_MAPS_API_KEY` is **required** —
the app fails to start without it:

```bash
fly secrets set -a routebench \
  R2_ENDPOINT="https://<accountid>.r2.cloudflarestorage.com" \
  R2_ACCESS_KEY_ID="..." \
  R2_SECRET_ACCESS_KEY="..." \
  R2_BUCKET="routebench" \
  WEB_ORIGIN="https://routebench-web.fly.dev" \
  GOOGLE_MAPS_API_KEY="AIza..." \
  ADMIN_TOKEN="$(openssl rand -hex 32)"

# Optional — enables written prose instead of templated findings:
fly secrets set -a routebench ANTHROPIC_API_KEY="sk-ant-..."
```

(If you switched to `MATRIX_ENGINE=osrm`, drop `GOOGLE_MAPS_API_KEY` and make
sure the OSRM sidecar from step 1 is deployed instead.)

`WEB_ORIGIN` **must** match the web app's public URL exactly, or the browser's
cross-origin calls are refused and the upload button fails with no server error.
If you put the web app on a custom domain, set `WEB_ORIGIN` to that domain.

Deploy, stamping the build with the commit so `/health` reports a real version:

```bash
fly deploy -c fly.toml --build-arg GIT_SHA=$(git rev-parse HEAD)
```

Verify:

```bash
curl -s https://routebench.fly.dev/healthz | jq
# status: "ok", checks.storage_writable: true,
# matrix_engine: "google", matrix_mode: "google", grade_available: true
```

On the Google engine, `/healthz` reports readiness from configuration — it does
**not** make a paid Google call on every check (Fly hits it every 15s). The one
hard 503 is unwritable **storage**: with nowhere to write a session the API
cannot function. A degraded matrix at request time (a Google quota error) falls
back to haversine with the grade withheld for that run, but the service stays up.

(On `MATRIX_ENGINE=osrm` the body instead reports `osrm_reachable` and, when
OSRM is down, `matrix_mode: haversine_estimates` with a `degraded` status and
still-200 — the same "serves estimates, never pulled from rotation" contract.)

---

## 3. Web frontend

The API base URL is **baked into the build** (`NEXT_PUBLIC_*` is compiled into
the client bundle, not read at runtime), so it is a `--build-arg`, and changing
the API URL later means rebuilding this app:

```bash
fly deploy -c fly.web.toml \
  --build-arg NEXT_PUBLIC_API_BASE=https://routebench.fly.dev
```

Then open `https://routebench-web.fly.dev` and run the sample fleet through it.

---

## 4. Validate the live site

Open the URL and upload the sample by hand, or run the smoke test against the
deployed stack — it drives the real path (pick a file, upload, watch it run,
read the result) end to end and fails loudly if CORS, storage, or rendering is
wrong:

```bash
cd web
E2E_LIVE=1 E2E_BASE_URL=https://routebench-web.fly.dev \
  npx playwright test live-smoke
```

It is the same test used locally against `http://localhost:3000`, and it is
skipped in normal CI (which has no live backend), so it only runs when you point
it at a stack. A green run is the real proof the deploy works — not just that
the machines are up.

---

## Environment variables reference

Set as Fly **secrets** (sensitive) or in `fly.toml` `[env]` (not sensitive).

| Variable | Where | Required | Notes |
| --- | --- | --- | --- |
| `STORAGE_BACKEND` | fly.toml env | yes | `s3` in production |
| `R2_ENDPOINT` | secret | yes | R2/S3 endpoint URL |
| `R2_ACCESS_KEY_ID` | secret | yes | |
| `R2_SECRET_ACCESS_KEY` | secret | yes | |
| `R2_BUCKET` | secret or env | yes | default `routebench` |
| `R2_REGION` | env | no | default `auto` (R2) |
| `MATRIX_ENGINE` | fly.toml env | no | `google` (default here) or `osrm` |
| `GOOGLE_MAPS_API_KEY` | secret | yes (google engine) | billed per element; see cost model |
| `OSRM_HOST` | fly.toml env | only osrm engine | `http://routebench-osrm.internal:5000` |
| `WEB_ORIGIN` | secret | yes | exact web origin, for CORS |
| `ANTHROPIC_API_KEY` | secret | no | omit → templated prose |
| `ADMIN_TOKEN` | secret | recommended | gates `/admin/*`; empty fails closed |
| `MATRIX_CACHE_PATH` | fly.toml env | no | on the volume, set already |
| `DAILY_BUDGET_USD` | env | no | LLM spend cap; degrades, does not 503 |
| `NEXT_PUBLIC_API_BASE` | web build-arg | yes | the API's public URL |

---

## The Google engine cost model

The default engine, Google Routes, needs the **Routes API** enabled with
billing; traffic-aware requests use the Advanced tier. It bills per **element**
(origins × destinations), and RouteBench's fleet benchmark is a full
all-stops-to-all-stops matrix, so a single **42-stop fleet ≈ 1,800 elements
≈ $18**. Cost grows with the square of fleet size.

This buys continent-wide (in fact worldwide) coverage and live traffic with no
self-hosted graph and no big machine. Behaviour:
- Times are real traffic-adjusted durations; the free-flow band multiplier is
  not applied on top.
- Any failure — bad key, quota exhausted, outage — falls back to haversine
  estimates with the grade withheld, the same graceful path as an OSRM outage.
- The engine is chosen at startup; with no key the app fails to start, so a
  misconfiguration surfaces immediately rather than mid-analysis.

**Cap the spend.** Set `DAILY_MATRIX_BUDGET_USD` to a daily ceiling — once the
day's estimated matrix spend reaches it, runs fall back to haversine estimates
(grade withheld) instead of billing more, resetting at UTC midnight. It is off
by default (`0`). Set it well above a normal day so it is a runaway-bill
backstop, not a routine limiter:

```bash
fly secrets set -a routebench DAILY_MATRIX_BUDGET_USD=200
```

This is a courtesy, not enforcement — it meters an *estimate* (the no-cache
worst case) and cannot stop a call already in flight. Also put a hard
**budget/quota on the key in the Google Cloud console** as the real ceiling.

HERE is a natural second engine — the selector and provider seam are built for
it — but only Google and OSRM ship today.

## Alternative: self-host OSRM instead of Google

If your volume is high and concentrated in a few regions, a self-hosted OSRM
graph has no per-request cost and can be cheaper than Google above roughly
15–30 fleet analyses/day. It gives free-flow times (no live traffic) and only
covers the region(s) you build. To switch:

1. Set `MATRIX_ENGINE = "osrm"` in `fly.toml` `[env]`, and remove the
   `GOOGLE_MAPS_API_KEY` secret (`fly secrets unset GOOGLE_MAPS_API_KEY -a routebench`).
2. Deploy the OSRM sidecar (step 1 above) for the region you serve.
3. Redeploy the API. `/healthz` will then report `matrix_mode: "osrm"`.

A continent-scale graph (all of North America ≈ 18 GB extract → ~40–60 GB
processed) needs a **large, always-on machine (~32–64 GB RAM)** and cannot use
the baked-graph image — mount the graph on a volume instead. Query latency does
**not** grow with graph size (OSRM's MLD keeps queries fast at any scale); the
cost is RAM, disk, build time, and a much bigger monthly bill. For broad coverage
without that, stay on Google.

## What is NOT set up

- **No CD.** Deploys are the manual `fly deploy` commands above. A GitHub Action
  could run them on push to `main` once you are happy deploying that way.
- **No custom domain.** The `.fly.dev` hostnames work out of the box; add a
  domain with `fly certs` and update `WEB_ORIGIN` + `NEXT_PUBLIC_API_BASE`.
- **Single API machine.** The budget ledger is a single-writer append, so the
  API is not horizontally scaled (`min`/`max` one machine). This is fine for the
  expected load; revisit if it changes.
- **OSRM is publicly reachable.** `routebench-osrm` has an `http_service`, so it
  gets a public `*.fly.dev` URL with no auth — anyone who finds it can use your
  OSRM as a free routing service and run up compute. The API only ever calls it
  over the private `.internal` network, so once you have confirmed the deploy you
  can drop OSRM's public IPs to make it internal-only:
  ```bash
  fly ips list -a routebench-osrm      # see what is allocated
  fly ips release <ip> -a routebench-osrm
  ```
  Left as a deliberate post-deploy step rather than baked in, because a
  misconfigured private-networking change is hard to tell apart from "OSRM is
  simply down" — verify the happy path first, then lock it down.
