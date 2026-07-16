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

## Where it came from

Derived from the adversary half of
[autonomous-sdlc](https://github.com/rslayer/autonomous-sdlc), with the builder
removed so it only ever reports. The same harness runs on `bhulan`, where its
first tuned run found 18 defects across 5 classes.
