# Robustness harness

An adversarial probe that tries to break RouteBench and reports what it finds.
It never fixes anything, and it never touches product code.

## What it is

`.github/workflows/robustness.yml` runs a different-model agent (Sonnet) that
attacks the public surface — the upload API, the CSV validator, the config
models, the verifier, the budget ledger, storage — and for every real defect
writes a **reproducing failing test** under `tests/adversary/`, plus a narrative
report under `reports/adversary/robustness-N.md`. It opens a findings PR.

**A failing test here is the deliverable, not a problem.** Each one documents a
defect the adversary reproduced.

Two properties make it safe to run at any time:

- **It cannot edit the product.** Everything under `src/routebench/` is
  read-only to the agent, and a guard step fails the run if any file there
  changed. The promise is mechanical, not just prompted.
- **It needs no OSRM and no Docker.** `tests/adversary/conftest.py` stubs the
  matrix provider, so the whole analysis pipeline runs in-process.

## Running it

One-time setup (repo admin): add a `CLAUDE_CODE_OAUTH_TOKEN` secret to this
repo — Settings → Secrets and variables → Actions. Generate the value with
`claude setup-token`. The secret is per-repo; a token on another repo does not
carry over.

Then, on demand:

```bash
gh workflow run robustness.yml

# or narrow the probe
gh workflow run robustness.yml -f focus="CSV validation"
```

There is deliberately **no push trigger** — `claude-code-action` rejects the
`push` event, which would produce a no-op cycle and an empty PR. Actions → 
"Run workflow" does the same thing from the UI.

## When a findings PR lands

1. Read `reports/adversary/robustness-N.md`. It records every coverage
   category as DEFECT or CLEAN, so you can see what was probed and cleared, not
   just what broke.
2. Decide what to fix. Not every finding deserves a fix — the report is input to
   that judgement, not a mandate. Fixing happens in `src/routebench/`, which the
   harness never touches.
3. **Promote the tests you fixed.** Move the test out of `tests/adversary/` into
   the main suite (e.g. `tests/core/`, `tests/app/`) so it runs in CI forever
   after. This is the step that turns a finding into a permanent guard.
4. Leave the rest. An unfixed finding stays red in `tests/adversary/`, which is
   excluded from CI — it is a documented, reproducible backlog item.

## Why adversary tests are excluded from CI

`.github/workflows/ci.yml` runs `pytest tests/ --ignore=tests/adversary`.
Unfixed findings are *expected* to fail; including them would make CI red by
design and train everyone to ignore it. Promotion (step 3 above) is the
deliberate act that moves a test from "known defect" to "guarded behaviour".

## Run history

**Run 1** (2026-07-17, against `init-main`) found **8 defects across 4 classes**,
all real, all fixed in `dbaec0d`:

- **NaN coordinates bypassed validation.** `nan < -90` and `nan > 90` are both
  False, so the range guard never fired. On a depot row it was accepted silently
  and carried a NaN into routing and geojson.
- **Path traversal in storage keys.** `base / "/etc/passwd"` resolves to
  `/etc/passwd` — pathlib join semantics discard the base.
- **The caller's config never reached `validate_csv`.** Accepted, persisted,
  echoed back, ignored.
- **Non-object config JSON → 500** instead of 422.

It also *predicted*, in a test comment, that fixing the third would expose
unbounded config values causing further 500s. It was right.

Its tests are promoted to `tests/core/test_adversary_regressions.py`.

**Its report was missing** — the prompt said mandatory, the agent skipped it
anyway. Fixed two ways: the report is now the FIRST thing the agent writes and
is updated per category, and a build step fails the run if it is absent. Prose
was not enough; a gate is.

**Run 2** (2026-07-17, against `init-main`) found **9 defects across 6 classes**,
all real, all fixed. Its tests are promoted to
`tests/core/test_adversary_regressions_run2.py`.

- **Stored XSS in the HTML report.** The Jinja environment was built with
  `autoescape=False`, and `route_id` — taken verbatim from the upload, with no
  character restriction — is interpolated straight into `report.html`, which is
  served as `text/html`. A `<script>` tag in a route_id executed in the browser
  of whoever opened the report. The flag was not gratuitous: `{{ css }}` and the
  chart SVGs are genuine markup and must pass through raw, so the fix turns
  escaping on and wraps only those two in `Markup`. The charts embed `route_id`
  on their axes; Altair escapes it, which was checked rather than assumed.
- **Quadratic bounding-box check, in the request handler.** `_check_bounding_box`
  compared every pair of stops — O(n^2) — inside the synchronous part of
  `POST /sessions`. A single well-formed 3,000-stop route, comfortably inside the
  5,000-stop limit, burned ~1.7s of CPU. The function now measures what its name
  says: the box, in one pass.
- **`inf` accepted as a traffic speed factor.** `Field(gt=0)` does not exclude
  `inf`, because `inf > 0` is True. `1e400` parsed to `inf` and collapsed every
  adjusted travel time to zero. Now upper-bounded and `allow_inf_nan=False`.
- **A misspelled config key was silently ignored.** Pydantic's default is
  `extra="ignore"`, so `work_rules.mx_shift_hours` fell back to the default and
  the caller got a clean 202 for an analysis that ignored the constraint they
  set. Now `extra="forbid"` on every config model.
- **A negative `service_time_minutes` was a 500.** `Stop`'s own constraints raise
  *pydantic's* `ValidationError`, a different class from this package's, so the
  `except` clauses never caught it and it escaped the handler uncaught.
- **A fractional `stop_sequence` was silently truncated.** `cast(pl.Int64)` on a
  float column truncates rather than raising, so `0.9` became `0` — and `0` is
  what marks the depot. A typo quietly promoted a delivery to the depot.

Fixing the last one surfaced a seventh, unprompted by the harness: `validate_csv`
ended with a hardcoded `is_valid=True`, so any error accumulated while building
rows would have returned a **quietly truncated fleet marked valid**. That is the
same class as run 1's `enforce_time_windows`: a value computed and then ignored.

**The gate worked; it was pointed at the wrong thing.** Run 1's report was
missing entirely, so run 2 made the file mandatory — and got a file. The agent
wrote the skeleton first as instructed and then never updated a row: all eleven
categories still read "NOT YET PROBED" beside nine real findings. Checking that
the file exists is not checking that it says anything. The next iteration needs
to assert on content, not presence.

**Run 3** (2026-07-17, against `init-main`) found **5 defects across 5 classes**,
all real, all fixed. Its tests are promoted to
`tests/agent/test_verifier_number_evasion.py` and
`tests/analysis/test_time_window_regressions.py`.

- **Two verifier evasions, both defeating the Phase 10.5 hardening.** A number
  glued to a unit letter ("4700min") sat between two word chars, so
  `_NUMBER_RE`'s trailing `\b` matched nothing and the figure was never
  extracted. And `_mask_identifiers`' plain `str.replace` erased a finding_id
  even as the PREFIX of a larger fabricated number, leaving a lone leftover
  digit that matched a real value — a fabricated $123M passed clean. Fixed with
  `(?!\d)` and whole-token `(?<!\w)id(?!\w)` masking.
- **Contradictory time window inflated idle time.** A window closing before it
  opens made the scoring path idle ~9 hours toward an impossible open time. The
  benchmark path already widened such a window to no constraint; the scoring
  path — what the report shows — did not.
- **Malformed time silently dropped.** An unparseable time was swallowed to
  None, indistinguishable from an absent column, while every other bad field
  surfaces something. Same class as run 2's fractional `stop_sequence`.

**The gate found a bug in itself.** Run 2's fix made the report mandatory AND
asserted on content — but with two bugs of its own, both of which run 3 walked
into. It counted the term "NOT YET PROBED" everywhere including the status
legend that *defines* it, so it failed a fully-filled report on one legend line;
and on failure it OVERWROTE the report with a stub, destroying run 3's genuinely
complete coverage record (saved only by an afterthought side-copy). Both fixed:
count only category rows, and prepend a banner instead of overwriting. A gate
must not destroy the artifact it is judging.

## Where it came from

Derived from the adversary half of
[autonomous-sdlc](https://github.com/rslayer/autonomous-sdlc), with the builder
removed so it only ever reports. The same harness runs on `bhulan`, where its
first tuned run found 18 defects across 5 classes.
