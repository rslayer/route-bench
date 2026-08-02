# Large-ops scaling (10k–20k stops): status and plan

## Where we are today

RouteBench evaluates an *existing* plan. Every stop already has a route and a
sequence; the tool measures how good that plan is and, where it can, re-solves
each route to an optimum to quantify the gap. Two costs scale with fleet size:

1. **Matrix fetch** — travel time/distance between stops. Bounded per route, but
   summed across the fleet. On the metered engine (Google) this is dollars.
2. **Optimal re-solve** — OR-Tools TSPTW per route, plus one fleet-wide VRPTW.
   Time-unbounded: guided local search runs to its full time limit every route.

### The graceful ceiling (shipped — "quick win")

Large fleets no longer hit a hard error. The behaviour is now tiered:

| Fleet size | Behaviour |
|---|---|
| ≤ 50 routes **and** ≤ 5,000 stops | Full analysis: descriptive metrics + per-route optimal re-solve. Unchanged. |
| ≤ 300 stops, ≥ 2 routes, shared depot | Also runs the fleet-wide VRPTW benchmark. Unchanged. |
| Above the route/stop benchmark caps, up to **1,000 routes / 50,000 stops** | **Accepted, analysed descriptively.** The optimal re-solve is skipped; the grade falls back to a nearest-neighbour sequencing baseline. The reason appears in `analyses_skipped`. |
| Above 1,000 routes or 50,000 stops | Rejected at validation (a box-safety ceiling, not a product limit). |

Caps live in one place each: `analysis/benchmark/fleet_matrix.py`
(`MAX_ROUTE_BENCHMARK_STOPS`, `MAX_ROUTE_BENCHMARK_ROUTES`,
`MAX_FLEET_BENCHMARK_STOPS`) and `core/schemas.py` (`MAX_FLEET_ROUTES`), with
`core/validation.py` reporting the friendly errors.

**Cost guard.** On Google, a large descriptive run still fetches per-route
matrices. The daily matrix budget (`DAILY_MATRIX_BUDGET_USD`) reserves worst-case
spend up front and degrades the run to straight-line (haversine) estimates once
the cap is hit — at which point the grade is withheld, as for any approximate
matrix. So a large upload cannot run up an unbounded bill; it self-limits to the
daily cap and then degrades. Confirm the cap is set to a sane value before
inviting large uploads.

### What "descriptive mode" does and does not give you

- **Gives**: every metric computed from the plan itself — distance, drive/service
  time, stops-per-mile density, shift compliance, window compliance, territory
  overlap, dispatch balance, outliers, reachability — and a grade blended from
  them, with sequencing scored against a cheap nearest-neighbour tour.
- **Does not give**: the "% worse than optimal" gap per route or across the
  fleet, because that requires the OR-Tools re-solve we skip at scale.

This is honest and useful, but it is not the differentiated claim ("your plan is
N% off optimal") that the benchmark makes at small scale. Closing that gap at
scale is the real project below.

---

## The real large-ops engine (scoped, not built)

Goal: produce a *comparative* grade — plan vs optimal — for 10k–20k-stop fleets,
in a web-request-friendly time, at acceptable cost.

### Why the current path cannot just be un-capped

- **Matrix cost/latency.** A single fleet-wide matrix at 15k stops is ~225M
  elements. On Google that is both cost-prohibitive and slow. Even self-hosted,
  a 15k×15k table is 225M cells — too large to build or hold whole.
- **Solver blowup.** One VRPTW over 15k stops does not converge usefully inside
  any web time limit; OR-Tools degrades badly well before that.

The answer is the standard decomposition: **cluster → solve-in-parallel →
stitch.**

### Architecture

1. **Cluster** the stops into solvable cells (target ~200–400 stops each).
   - Spatial + capacity-balanced partitioning (k-means seed, then balance by
     vehicle capacity / shift length). Respect existing route assignments where
     the intent is to grade the operator's own territories rather than re-cut
     them.
   - Clustering is O(n·k) — cheap next to solving.
2. **Solve each cluster independently, in parallel.**
   - Reuse the existing TSPTW/VRPTW path per cluster, each with its own
     size-scaled time budget (the adaptive budget already in
     `analysis/benchmark/budget.py`).
   - Parallelism is the whole point: N clusters solve concurrently, so
     wall-clock is one cluster's solve, not the sum.
3. **Stitch** the per-cluster results into one fleet-level grade.
   - Sum measured vs optimal by cluster, stop-weighted, exactly as the current
     `_stop_weighted_gap` does across routes — the reduction already exists;
     it just needs to run over clusters.
   - Inter-cluster effects (a stop that would be better served from a
     neighbouring cluster) are a known, bounded approximation — disclose it.

### The hard dependency: self-hosted OSRM as the default matrix engine

None of the above is affordable on a metered matrix API. It is gated on making
**OSRM the matrix backend for large fleets.**

Good news: the OSRM engine and its deploy already exist.

- `infra/matrix/osrm.py` — the provider, with request chunking for large tables.
- `fly.osrm.toml` + `osrm.Dockerfile` — an always-on sidecar
  (`routebench-osrm`) with the road-network graph baked into the image,
  reachable from the API over Fly's private network at
  `http://routebench-osrm.internal:5000`.
- `MATRIX_ENGINE` selects the engine; today the API runs on `google`.

What remains for the matrix side:
- **Deploy the OSRM sidecar** for the target region(s) and point large-fleet runs
  at it (either flip `MATRIX_ENGINE=osrm`, or route only large fleets to OSRM and
  keep Google's live-traffic accuracy for small ones).
- **Never materialise the full NxN.** Build only the per-cluster matrices (plus a
  small inter-cluster skeleton if we model hand-offs). OSRM's chunking helps, but
  the caller must ask for cluster-sized tables, not the whole fleet.
- **Region coverage.** The baked graph is one region per image; multi-region
  uploads need either a larger extract or per-region routing.

### Work breakdown (rough)

| Piece | Effort | Notes |
|---|---|---|
| Deploy OSRM sidecar + wire large fleets to it | S–M | Infra exists; needs a real deploy, region choice, and a routing rule. |
| Clustering module (spatial + capacity-balanced) | M | New; the interesting design work is the balance objective. |
| Parallel per-cluster solve orchestration | M | Reuses the existing solver + adaptive budget; adds a concurrency layer and progress. |
| Stitch + fleet-level comparative grade | S–M | Extends the existing stop-weighted gap over clusters. |
| Disclose the inter-cluster approximation in the report | S | Consistent with the existing degrade banners. |
| Load/soak test at 10k–20k on real geography | M | The step that turns "should work" into "does." |

Net: a **multi-week** build, not a cycle — and it should land behind OSRM, not
in front of it. The quick win above buys us a truthful "descriptive analysis at
scale" today while this is built.

### Open product question

At 10k–20k stops, is the user asking "grade my whole plan" or "grade each
territory"? The former wants the fleet-wide comparative number (and the
inter-cluster approximation matters); the latter is nearly free — it is the
per-cluster grades without stitching, and it sidesteps the hardest part. Worth
settling before building, because it changes what "optimal" even means here.
