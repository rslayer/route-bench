# RouteBench Robustness Audit — Run 3

Adversarial probing of `src/routebench` per the 11-category checklist.
`src/routebench/` is READ-ONLY for this run; findings are reproduced as
failing pytest tests under `tests/adversary/`.

Status legend: DEFECT (bug found, test written), CLEAN (handled correctly,
no test needed), NOT YET PROBED (not started).

| # | Category | Status | Notes |
|---|----------|--------|-------|
| 1 | CSV structure (header/BOM/CRLF/ragged/quotes/huge field) | CLEAN | No header (first row consumed as header, then MISSING_REQUIRED_COLUMN), header-only (0 rows, valid empty fleet), duplicate columns (polars auto-suffixes, first occurrence used), BOM, CRLF, ragged short/long rows, embedded quotes/newlines, trailing commas, blank lines, 2MB single field, empty file, whitespace-only file — all handled without crash, correct 422 on genuinely bad input. |
| 2 | Coordinates (out of range, NaN, Inf, swapped, precision) | CLEAN | lat>90/<-90, lon>180/<-180, NaN, Inf, 1e400 (→inf) all correctly rejected with OUT_OF_RANGE (confirms the run-2 NaN fix holds). (0,0) rejected as ZERO_COORDINATES. Extreme-precision floats (1e-15 range) accepted and processed without error. |
| 3 | Wrong types (null/string/bool/array, negative service time) | CLEAN | null stop_sequence, non-numeric latitude, boolean-as-stop_sequence, non-numeric stop_sequence all rejected with clean 422-shaped errors (INVALID_TYPE/NULL_REQUIRED_FIELD). Negative service_time_minutes correctly rejected via pydantic (confirms run-2 fix holds — no 500). |
| 4 | stop_sequence (missing depot, dup, non-contiguous, negative, multi-depot) | CLEAN | Missing depot, duplicate stop_sequence, non-contiguous sequence, negative sequence (surfaces as MISSING_DEPOT since sort puts it first — correct rejection, if slightly imprecise messaging), gigantic sequence value, multiple depots per route — all correctly rejected, no crash. |
| 5 | Times (non-monotonic, dup, far-future, pre-epoch, tz, windows) | DEFECT (2) | (a) A time window that closes before it opens (start=17:00, end=08:00) is accepted as valid with zero warnings, then `compute_time_metrics` silently produces ~9 hours of bogus idle time instead of being widened/flagged the way the benchmark solver's `windows.py` already does. Test: `test_time_window_closes_before_opens_silently_wrong.py`. (b) An unparseable time/datetime string is silently discarded with no error, warning, or `defaults_applied` entry — indistinguishable from an absent field. Test: `test_malformed_time_silently_dropped.py`. Far-future (year 9999), pre-epoch (1800), and tz-aware datetimes were all otherwise accepted without crash. |
| 6 | Enormous payloads (50k+ stops, near 50MB cap) | CLEAN | 50 routes × 99 stops (4,950 stops, at the route-count boundary) validated in 0.06s. 51 routes correctly rejected with TOO_MANY_ROUTES in <0.01s (no quadratic blowup — confirms the run-2 bounding-box O(n²)→O(n) fix holds at this scale). |
| 7 | Unicode/control chars/injection-looking values | CLEAN | RTL override chars, null bytes, `<script>` tags, SQL-injection-shaped strings, 100k-char route_id, emoji/combining chars, format-string patterns, `=1+1` CSV-formula-shaped values, raw control chars — all pass through validate_csv as opaque strings with no crash. Confirmed the report template (`document.py`) has Jinja2 `autoescape=True` and only wraps internally-generated SVG/CSS in `Markup(...)`, never user-derived prose slots — the stored-XSS class from run 2 stays fixed. |
| 8 | config JSON (unknown fields, bad traffic profile, bands, work rules) | CLEAN | Unknown top-level/nested fields, unknown traffic profile name, `max_shift_hours` 0/negative, `default_factor`/`speed_factor` 0/negative/inf/1e400, overlapping bands, midnight-wrapping bands, non-dict `work_rules`, deeply-nested garbage in place of a scalar — all correctly rejected with 422-shaped pydantic errors. Touching (non-overlapping) bands and `lunch_after_hours` exceeding `max_shift_hours` (lunch never triggers, benign) accepted, correctly. |
| 9 | Malformed transport (content-type, truncated multipart, nested JSON) | CLEAN | Missing file field (422), mismatched content-type (accepted, content-type isn't trusted for parsing), empty file (422 "Empty file uploaded"), non-UTF8 binary CSV (422 CSV_READ_ERROR), non-JSON config, config as array/number/string (422 "expected a JSON object"), a 5,000-deep nested JSON array in place of a scalar (422, no stack overflow), path-traversal-shaped filename (inert — server always writes as fixed "upload.csv"). |
| 10 | Verifier evasion (verify_slot false PASS) | DEFECT (2) | (a) A fabricated large number sharing a digit-prefix with a masked identifier (e.g. finding_id) has that prefix silently erased by `_mask_identifiers`' substring-based `.replace()`, leaving a short remainder that can coincidentally match real source data — full false PASS on an invented $123,456,789 figure. Test: `test_verifier_identifier_substring_evasion.py`. (b) A fabricated integer immediately followed by a unit-like letter suffix with no space/decimal ("4700min", "8500lbs", "950pct") can never satisfy `_NUMBER_RE`'s trailing `\b` word-boundary requirement, so it is never extracted at all — full false PASS. Test: `test_verifier_unit_suffix_number_evasion.py`. Comma-grouped fabrications, exact ±5%-tolerance-boundary values, and fabricated percentages were all correctly caught. |
| 11 | Storage keys (path traversal into read/write/read_object/append_object) | DEFECT | `GET /sessions/%2e%2e/report.html` → 200, serves a file outside the storage root. `_object_path` (used by read_object/append_object) confines the resolved path; `_session_dir` (used by read/write/exists/delete_session) does not. A literal `..` in the URL is normalized away by the HTTP client, but `%2e%2e` reaches the server intact and decodes to `..` server-side. Test: `test_storage_path_traversal.py`. |

## Findings detail

### 5. Times — contradictory window silently inflates idle time (DEFECT)

`validate_csv` never checks `time_window_start < time_window_end`. A row with
`start=17:00, end=08:00` (impossible to ever satisfy) validates cleanly. The
benchmark solver's `routebench/analysis/benchmark/windows.py::stop_window`
explicitly detects this contradiction, logs a warning, and widens the window
to the full horizon. `routebench/analysis/scoring/time.py::compute_time_metrics`
— the path that actually drives the report the user sees — has no equivalent
guard: it treats `time_window_start` as an unconditional "wait until this
time" instruction, so a stop with this contradictory window makes the
simulated vehicle idle for ~9 hours waiting for a window it can only ever
miss, silently inflating `idle_time_hours` and `total_time_hours` with
nothing in the `ValidationReport` or anywhere else indicating the input was
bad. Confirmed via direct `compute_time_metrics` call: `idle_time_hours` ≈
9.17 for a 5-minute depot-to-stop hop. Test:
`test_time_window_closes_before_opens_silently_wrong.py`.

### 5. Times — malformed time strings silently dropped (DEFECT)

`_parse_datetime`/`_parse_time` in `routebench/core/validation.py` catch
`ValueError` on an unparseable string and return `None` — identical to how a
genuinely blank field is handled. Unlike every other malformed-value path in
the same function (bad numeric casts raise `INVALID_TYPE`, defaulted-but-present
fields get a `DefaultApplied` entry), a garbage `planned_arrival_time` such as
`"not-a-real-time-at-all!!"` produces zero errors, zero warnings, and zero
`defaults_applied` entries — the `ValidationReport` looks identical to one
where the column was simply absent. Confirmed via direct `validate_csv` call.
Test: `test_malformed_time_silently_dropped.py`.

### 10. Verifier evasion — identifier-substring masking (DEFECT)

`_mask_identifiers` blanks known identifiers (route IDs, finding IDs,
required references) with `str.replace(identifier, " " * len(identifier))` —
a substring replace, not a whole-token match. `finding_id` is a 16-hex-char
`sha256(...).hexdigest()[:16]` prefix, which can be (or, for an adversarial
writer, can be engineered to be treated as) an all-digit string. When such an
identifier appears as the *prefix* of a longer fabricated number in the
prose, `.replace` strips only the identifier-length portion, leaving a short
residual digit string that can coincidentally equal an unrelated real source
value. Confirmed: prose citing finding `12345678` and claiming a fabricated
`$123456789` loss (nowhere in the source data) passes `verify_slot` cleanly
(`passed=True, issues=[]`), because masking left only a trailing `9` behind,
which happened to equal a real `total_routes: 9` in the source data. Test:
`test_verifier_identifier_substring_evasion.py`.

### 10. Verifier evasion — unit-suffixed numbers bypass extraction (DEFECT)

`_NUMBER_RE`'s plain-number branch (`\d+(?:\.\d+)?`) requires a `\b` word
boundary immediately after the digits. A boundary requires a transition
between a word character and a non-word character; when a bare integer is
immediately followed by a letter with no separating space or decimal point
("4700min", "8500lbs", "950pct"), every possible end position sits between
two word characters, so no substring of the digit run ever satisfies the
trailing boundary — the regex cannot match any part of the number and the
whole token is skipped. Confirmed: prose claiming "4700min" delay, "8500lbs"
overcapacity, and "950pct" over theoretical optimum — none present in or
near the source data — passes `verify_slot` cleanly with zero issues raised.
(A decimal-pointed variant like "45.2k" partially resists this because the
"." gives the regex a non-word character to backtrack to, so "45" alone gets
extracted and correctly flagged — the escape hatch is specific to bare
integer suffixes.) Test: `test_verifier_unit_suffix_number_evasion.py`.

### 11. Storage keys — path traversal via `session_id` (DEFECT)

`LocalStorageBackend._session_dir(session_id)` (`src/routebench/infra/storage/local.py`)
returns `self._base / session_id` with **no confinement check**, unlike its sibling
`_object_path(key)` two methods below, which explicitly resolves the candidate path and
raises if it escapes the root. Every download route
(`/sessions/{session_id}/report.html`, `report.pdf`, `analysis.json`, `routes.geojson`)
passes the raw URL path segment straight into `storage.exists(session_id, ...)` /
`storage.read(session_id, ...)` with no validation that `session_id` looks like the
uuid4-hex the app itself generates.

A literal `..` segment gets collapsed by any normalizing HTTP client before the request
is even sent (confirmed: httpx rewrites `/sessions/../report.html` to `/report.html`
client-side). But `%2e%2e` survives client-side normalization and is decoded to `..`
by the ASGI server, landing in `_session_dir` unconfined. Confirmed unauthenticated 200
response serving a file one directory above the configured storage root
(`GET /sessions/%2e%2e/report.html`). No admin token or session-ownership check is on
this path at all. Confined to one level of traversal per literal `..` segment (path
segments cannot contain `/`, even percent-encoded, since the ASGI server decodes before
routing), but that is still enough to read any predictably-named file placed adjacent to
the storage root.
