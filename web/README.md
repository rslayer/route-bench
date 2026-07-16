# routebench-web

The customer-facing app: upload a route plan, get a quality score.

Deploys as a **separate Fly app** from the API (`fly.web.toml`), so the two
scale and ship independently.

## Run it

```bash
npm install
npm run dev            # http://localhost:3000
```

It needs the API running and allowing this origin back:

```bash
# in the repo root — WEB_ORIGIN is the CORS allowlist
WEB_ORIGIN=http://localhost:3000 GIT_SHA=$(git rev-parse HEAD) \
  uv run uvicorn routebench.app.api.app:create_app --factory --port 8000
```

`NEXT_PUBLIC_API_BASE` points the client at the API (default
`http://localhost:8000`). It is inlined into the client bundle at build time, so
it is set with `--build-arg` on deploy, not as a runtime secret — it is a public
URL, not a secret.

## Checks

```bash
npm run typecheck
npm run lint
npm run test           # Playwright (arrives with the e2e slice)
```

## Layout

```
src/
  app/          routes (App Router)
  components/   shared UI
  lib/
    types.ts    hand-maintained mirrors of the Python contracts
    api.ts      API client; ApiError keeps the HTTP status, which IS the meaning
    schema.ts   the CSV schema — template, column reference, and mapper read it
    palette.ts  colorblind-safe route colors (Paul Tol muted)
```

## Two things to know

**The types are hand-maintained.** There is no codegen. `src/lib/types.ts`
mirrors the Python models and names the file each type came from; when you
change a contract on either side, change both. `docs/routes-geojson.md` in the
repo root is the geojson contract.

**Gaps can be negative.** `distance_gap_pct` and `improvement_gap_pct` may be
zero or below, meaning the solver found nothing better than the plan. That is a
real result — render it as "within solver reach", never as a saving, and never
clamp it.
