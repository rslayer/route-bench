# Deploying RouteBench to Fly.io

RouteBench is three services, deployed as three Fly apps:

| App | What | Config |
| --- | --- | --- |
| `routebench` | FastAPI API | `fly.toml` |
| `routebench-osrm` | OSRM routing engine, graph baked in | `fly.osrm.toml` |
| `routebench-web` | Next.js frontend | `fly.web.toml` |

Session artifacts live in **Cloudflare R2** (or any S3-compatible store), not on
the machine — Fly disks are ephemeral, and a redeploy would otherwise wipe every
report. Only the matrix cache sits on a small Fly volume.

This is a real deploy: it needs a Fly account, an R2 bucket, and it costs money
(three small machines plus object storage). None of it is automated — run the
steps below by hand the first time, in order.

---

## 0. One-time prerequisites

- `fly` CLI installed and `fly auth login` done.
- A Cloudflare R2 bucket (or S3/MinIO). Note its **endpoint URL**, **access key
  id**, **secret access key**, and **bucket name**.
- Optionally an Anthropic API key. Without it the analysis still runs — findings
  are filled from templates instead of written prose — so you can ship first and
  add it later.

Create the three apps (no deploy yet):

```bash
fly apps create routebench
fly apps create routebench-osrm
fly apps create routebench-web
```

---

## 1. OSRM (deploy first — the API waits on it)

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
`STORAGE_BACKEND=s3` and points `OSRM_HOST` at the sidecar; these are the values
it cannot ship in plaintext:

```bash
fly secrets set -a routebench \
  R2_ENDPOINT="https://<accountid>.r2.cloudflarestorage.com" \
  R2_ACCESS_KEY_ID="..." \
  R2_SECRET_ACCESS_KEY="..." \
  R2_BUCKET="routebench" \
  WEB_ORIGIN="https://routebench-web.fly.dev" \
  ADMIN_TOKEN="$(openssl rand -hex 32)"

# Optional — enables written prose instead of templated findings:
fly secrets set -a routebench ANTHROPIC_API_KEY="sk-ant-..."
```

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
# status: "ok", checks.storage_writable: true, checks.osrm_reachable: true,
# matrix_mode: "osrm", grade_available: true
```

`/healthz` returns **200 while OSRM is down** — `status: "degraded"`,
`matrix_mode: "haversine_estimates"`, `grade_available: false` — because the API
still serves (estimates, grade withheld) and the Fly health check must not pull
a working-but-degraded machine out of rotation. Only unreachable **storage** is
a hard 503, since with nowhere to write a session the API genuinely cannot
function. So if `matrix_mode` is `haversine_estimates`, the API cannot reach
OSRM — check step 1 and that `OSRM_HOST` in `fly.toml` matches the OSRM app
name — but the site is up.

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
| `OSRM_HOST` | fly.toml env | yes (osrm engine) | `http://routebench-osrm.internal:5000` |
| `MATRIX_ENGINE` | secret or env | no | `osrm` (default) or `google` |
| `GOOGLE_MAPS_API_KEY` | secret | if google | billed per element; see above |
| `WEB_ORIGIN` | secret | yes | exact web origin, for CORS |
| `ANTHROPIC_API_KEY` | secret | no | omit → templated prose |
| `ADMIN_TOKEN` | secret | recommended | gates `/admin/*`; empty fails closed |
| `MATRIX_CACHE_PATH` | fly.toml env | no | on the volume, set already |
| `DAILY_BUDGET_USD` | env | no | LLM spend cap; degrades, does not 503 |
| `NEXT_PUBLIC_API_BASE` | web build-arg | yes | the API's public URL |

---

## Optional: live traffic via Google instead of OSRM

By default RouteBench uses self-hosted OSRM, which returns free-flow times at no
per-request cost. To grade against **live traffic** instead, switch the matrix
engine to Google Routes:

```bash
fly secrets set -a routebench \
  MATRIX_ENGINE=google \
  GOOGLE_MAPS_API_KEY="AIza..."
```

The key needs the **Routes API** enabled with billing, and traffic-aware
requests use the Advanced tier. Understand the cost before switching: Google
bills per **element** (origins × destinations), so a single 42-stop fleet
benchmark is ~1,800 elements ≈ **$18**. It is off by default for exactly this
reason.

Behaviour when on:
- Times are real traffic-adjusted durations; the free-flow band multiplier is
  not applied on top.
- Any failure (bad key, quota, outage) falls back to haversine estimates with
  the grade withheld — the same graceful path as an OSRM outage.
- Selecting `google` with no key **fails at startup**, so a misconfiguration
  surfaces immediately rather than mid-analysis.
- If you run Google, you do not need the OSRM sidecar (step 1) at all.

HERE is a natural second engine — the selector and provider seam are built for
it — but only Google ships today.

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
