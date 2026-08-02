# RouteBench Robustness Audit — Run 4

Adversarial probing of `src/routebench` per the 11-category checklist.
`src/routebench/` is READ-ONLY for this run; findings are reproduced as
failing pytest tests under `tests/adversary/`.

This is run 4. Runs 1-3 already fixed 22 defects (8 + 9 + 5) across CSV
validation, config models, the verifier, and storage path traversal. This run
assumes that hardening is in place and looks for what it missed.

Status legend: DEFECT (bug found, test written), CLEAN (handled correctly,
no test needed), NOT YET PROBED (not started).

| # | Category | Status | Notes |
|---|----------|--------|-------|
| 1 | CSV structure (header/BOM/CRLF/ragged/quotes/huge field) | CLEAN | see detail |
| 2 | Coordinates (out of range, NaN, Inf, swapped, precision) | CLEAN | see detail |
| 3 | Wrong types (null/string/bool/array, negative service time) | DEFECT | 2 defects, see detail |
| 4 | stop_sequence (missing depot, dup, non-contiguous, negative, multi-depot) | CLEAN | see detail |
| 5 | Times (non-monotonic, dup, far-future, pre-epoch, tz, windows) | CLEAN | see detail |
| 6 | Enormous payloads (50k+ stops, near 50MB cap) | CLEAN | see detail |
| 7 | Unicode/control chars/injection-looking values | CLEAN | see detail |
| 8 | config JSON (unknown fields, bad traffic profile, bands, work rules) | DEFECT | unknown industry key silently accepted |
| 9 | Malformed transport (content-type, truncated multipart, nested JSON) | DEFECT | 2 defects, see detail |
| 10 | Verifier evasion (verify_slot false PASS) | DEFECT | sign-flip evasion |
| 11 | Storage keys (path traversal into read/write/read_object/append_object) | DEFECT | 2 defects, see detail |

## Findings detail

### Category 1 — CSV structure: CLEAN

Tried: no header, header-only (rejected NO_DATA_ROWS), duplicate column names
(polars renames the dup to `latitude_duplicated_0`, first column wins, extra
ignored — not a defect), UTF-8 BOM, CRLF line endings, ragged short rows
(missing trailing field → null → NULL_REQUIRED_FIELD), ragged long rows
(CSV_READ_ERROR, clean 422), embedded newlines/commas inside quoted fields,
trailing-comma unnamed column, blank lines mid-file (become NULL_REQUIRED_FIELD
rows), a 2MB single field. All either processed correctly or rejected with a
clear 422. Nothing crashed, nothing hung, nothing was silently misread.

### Category 2 — Coordinates: CLEAN

lat/lon just past the bound, string `"nan"`/`"NaN"`, `"inf"`/`"-inf"`, `1e400`
(parses to `inf`), swapped lat/lon (caught by the same range check since the
swapped values land out of range here), empty string (null), boolean literal
`true` (INVALID_TYPE). The NaN/Inf fix from run 1 (`not (-90 <= x <= 90)`
instead of `x < -90 or x > 90`) holds for all of these. `1e-15` (extreme
precision, not out of range) is accepted correctly and only trips the
(intentional) large-bounding-box warning once paired with a far-away stop.

### Category 3 — Wrong types: DEFECT (2)

- Non-numeric `service_time_minutes` string (e.g. `"abc"`) → uncaught
  `ValueError: could not convert string to float: 'abc'`, a 500 instead of a
  422. `tests/adversary/test_service_time_non_numeric_string_crash.py`.
- A `service_time_minutes` column that polars infers as **Boolean** (e.g. one
  blank cell + one `"True"` cell) → uncaught polars `InvalidOperationError`
  ("got invalid or ambiguous dtypes: `[bool, dyn float]`") when filling nulls
  with the float default, a 500 instead of a 422.
  `tests/adversary/test_service_time_boolean_column_crash.py`.

Everything else in this category was CLEAN: negative service time (proper
422, run 2's fix), demand_units/weight/volume wrong type or negative (safely
defaulted to None via `_safe_float`, or a clean 422 for negative), boolean
`stop_sequence`, null `route_id`, numeric-looking `route_id`.

### Category 4 — stop_sequence: CLEAN

Negative stop_sequence (sorts before 0, correctly rejected as MISSING_DEPOT —
message is a little misleading but the input is correctly refused), a
`stop_sequence` too large for i64 (CSV_READ_ERROR, clean 422), duplicate depot
rows (DUPLICATE_STOP), non-contiguous sequence, duplicate non-depot stop,
negative fractional stop_sequence (INVALID_TYPE, run 2's fix generalizes).

### Category 5 — Times: CLEAN

Far-future (`9999-12-31`) and pre-epoch (`1600-01-01`) timestamps parse and
are accepted — no explicit bound, but nothing downstream depends on
`planned_arrival_time` for scoring math (only `report/geojson.py` and display;
confirmed by grep), so this is a display-only latitude, not a computation
bug. Timezone-aware vs naive `planned_arrival_time` both parse via
`datetime.fromisoformat`. Windows that close before they open, and windows
that span midnight (e.g. 22:00-02:00, which the model does not represent as
wraparound), both correctly produce a `CONTRADICTORY_TIME_WINDOW` warning and
are treated as no constraint per run 3's fix — not silent. Garbage
`planned_start_time` on the depot row falls back to the default start
correctly.

### Category 6 — Enormous payloads: CLEAN

A 50-route x 100-stop fleet (4,950 stops, near the 5,000 cap) validates in
~0.06s — the O(n^2) bounding-box bug from run 2 stays fixed. A single route
with 5,000 stops (the per-route contiguity + total-stop cap) also validates
in ~0.06s. No quadratic blowup found in the synchronous validation path that
`POST /sessions` runs before enqueuing.

### Category 7 — Unicode / control / injection-looking values: CLEAN

Unicode (☃), NUL and control bytes, RTL override characters, and
script-tag/SQL-injection-shaped strings in `route_id`/`address`/`customer_id`
all pass through `validate_csv` as opaque strings with no crash. A 100KB
`route_id` is accepted (no explicit length cap, but bounded by the 50MB
upload cap overall — not pursued further as a defect since nothing crashes or
mis-renders at this layer). The stored-XSS class from run 2 (unescaped
`route_id` in the HTML report) was already fixed at the Jinja layer
(`autoescape=True`); not re-broken here.

### Category 8 — config JSON: DEFECT (1)

`AnalysisConfig.traffic` validates a named profile string against
`NAMED_TRAFFIC_PROFILES` and rejects an unknown name with a clear 422. But
`AnalysisConfig.industry: str | None` has **no equivalent validation** against
`INDUSTRY_PROFILES` — any string is accepted. Downstream, `get_profile(key)`
(`core/industry.py`) does a plain `dict.get`, so an unrecognized/typo'd
industry key resolves to `None` and the pipeline silently runs with **no**
industry profile: no industry-specific grading weights, and no
implausible-service-time flagging (`analysis/diagnosis/service_sanity.py`
explicitly skips its check when `industry_profile is None`, per its own
comment "No industry chosen -> no band to judge against"). A caller who
believes they selected `"courier"` but typo'd `"courrier"` gets a materially
different, unflagged analysis with zero error or warning anywhere.
`tests/adversary/test_unknown_industry_silently_ignored.py`.

Everything else in category 8 was CLEAN: unknown top-level field
(extra_forbidden), overlapping traffic bands (rejected), touching-but-not-
overlapping bands (accepted, correct), a band that wraps past midnight
(rejected with a clear message), `speed_factor` 0 / negative / `1e400` / NaN
(all rejected — the `allow_inf_nan=False` + upper bound from run 2 holds),
`max_shift_hours` 0/negative (rejected), `lunch_after_hours` set past
`max_shift_hours` (accepted — that's a semantic oddity for the scheduler to
reason about, not a validation crash, so not pursued as a defect), wrong
type for `work_rules`/`traffic` (clean model_type errors), a deeply nested
dict value in place of a scalar (clean float_type error), a malformed
`grading_weights` shape (clean model_type error).

### Category 9 — Malformed transport: DEFECT (2)

- A deeply nested `config` JSON array (10,000+ levels of `[[[...]]]`) makes
  `json.loads` raise Python's built-in `RecursionError`. `POST /sessions`
  only catches `json.JSONDecodeError` around that call, so `RecursionError`
  propagates uncaught → 500 instead of 422. This is a trivially-sized request
  body (a few tens of KB) causing an unhandled crash — a cheap DoS vector.
  `tests/adversary/test_config_deeply_nested_json_recursion_crash.py`.
- `POST /admin/sessions/{session_id}/replay` (admin-token-gated) calls
  `storage.read(session_id, "analysis.json")` directly and only catches
  `FileNotFoundError`. Every other artifact route goes through
  `_serve_artifact`, which calls `storage.exists()` first — and `exists()`
  catches the `ValueError` that a traversal-shaped id raises during path
  confinement, turning it into a clean 404. The replay endpoint skips that
  guard, so a percent-encoded traversal id (`%2e%2e`, which — per the
  existing comment in `local.py` — survives client-side normalization and
  decodes server-side) raises `ValueError` uncaught → 500 instead of 404.
  Requires the admin token, so the blast radius is smaller, but it is the
  same "uncaught path-confinement ValueError" bug class run 3 fixed
  everywhere else, just missed on this one route.
  `tests/adversary/test_admin_replay_path_traversal_crash.py`.

Everything else was CLEAN: wrong `Content-Type` on the file part (validated
by content, not header, so this doesn't matter), non-CSV binary bytes (clean
422 via MISSING_REQUIRED_COLUMN), empty filename, missing `file` field
entirely, a very large invalid-JSON `config` string, a `config` string with
an embedded NUL byte — all clean 422s.

### Category 10 — Verifier evasion: DEFECT (1)

`_NUMBER_RE` has no leading sign handling: a token like `-42` matches only
the digits `42` (the `\b`/`(?!\d)` boundaries never look at the preceding
`-`), so the extracted claim is `42.0`, not `-42.0`. If the source data
contains `42` (a positive figure — e.g. `delta_minutes: 42.0`) but not `-42`,
prose fabricating `"-42"` (a materially different claim — a decrease/loss
where the source shows an increase/gain, or vice versa) still verifies
against the *magnitude* and passes. Reproduced: source has
`{"finding_id": "abc123", "delta_minutes": 42.0}`; prose "Idle time changed
by -42 minutes this week, a concerning regression." → `verify_slot` returns
`passed=True`. A false PASS on a sign-flipped, fabricated claim.
`tests/adversary/test_verifier_sign_flip_evasion.py`.

Retested the three evasions run 3 already closed (unit-suffixed numbers,
identifier-substring erosion, percentages near 100/near the tolerance edge) —
all still correctly flagged.

### Category 11 — Storage keys: DEFECT (2)

- The admin-replay `ValueError`-uncaught bug above is also a category-11
  finding (a path-traversal-shaped *session id*, not an object key, but the
  same `_confine`/`ValueError` mechanism). Counted once, under category 9.
- `LocalStorageBackend._object_path("")` resolves to the exact same path as
  the reserved `_OBJECTS_DIR` ("_objects") itself, because `Path.joinpath("")`
  is a no-op. If the `_objects` directory does not exist yet,
  `append_object("", data)` opens that path in `"ab"` mode, which — since
  nothing exists there — **creates a plain file** named `_objects` instead of
  the directory every other object key needs as a parent. Every subsequent
  internal write through a real key (e.g. `BudgetTracker`'s
  `ledger/2026-08-02.jsonl`) then fails with `NotADirectoryError` inside
  `p.parent.mkdir(...)`. `BudgetTracker.record_spend` swallows this
  (`except Exception: logger.exception(...)`) and `today_spend` swallows the
  matching read failure the same way, so the daily budget cap goes **silently
  and permanently** to "always $0 spent today" for the remainder of the
  process — a silent budget-cap bypass, not a crash. No current caller passes
  an empty key (`ledger_key()` always produces a non-empty key), so this is
  not reachable through `POST /sessions` today — the task explicitly calls
  out `read_object`/`append_object` as library surface worth attacking
  directly, and this is a real latent defect in that surface: the emptiness
  check that exists for `session_id`/`filename` (`Session id`/`Session path`
  confinement) has no equivalent "must be non-empty" guard for object keys.
  `tests/adversary/test_empty_object_key_corrupts_objects_dir.py`.

Direct traversal keys (`"../x"`, `"/etc/passwd"`, `"a/../../b"`) into both
`read_object` and `append_object` are correctly refused with `ValueError`
(run 1's `_confine` fix holds). Session-id and session-filename traversal
into the normal session artifact routes (`GET /sessions/{id}/report.html`
etc.) is correctly caught via `exists()` → 404, per run 3's fix — the gap is
specifically the admin replay route, which bypasses that guard.
