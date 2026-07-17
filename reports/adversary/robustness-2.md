# RouteBench Robustness Audit — Run 2

Adversarial probing of `src/routebench` per the 11-category checklist.
`src/routebench/` is READ-ONLY for this run; findings are reproduced as
failing pytest tests under `tests/adversary/`.

Status legend: DEFECT (bug found, test written), CLEAN (handled correctly,
no test needed), NOT YET PROBED (not started).

| # | Category | Status | Notes |
|---|----------|--------|-------|
| 1 | CSV structure (header/BOM/CRLF/ragged/quotes/huge field) | NOT YET PROBED | |
| 2 | Coordinates (out of range, NaN, Inf, swapped, precision) | NOT YET PROBED | |
| 3 | Wrong types (null/string/bool/array, negative service time) | NOT YET PROBED | |
| 4 | stop_sequence (missing depot, dup, non-contiguous, negative, multi-depot) | NOT YET PROBED | |
| 5 | Times (non-monotonic, dup, far-future, pre-epoch, tz, windows) | NOT YET PROBED | |
| 6 | Enormous payloads (50k+ stops, near 50MB cap) | NOT YET PROBED | |
| 7 | Unicode/control chars/injection-looking values | NOT YET PROBED | |
| 8 | config JSON (unknown fields, bad traffic profile, bands, work rules) | NOT YET PROBED | |
| 9 | Malformed transport (content-type, truncated multipart, nested JSON) | NOT YET PROBED | |
| 10 | Verifier evasion (verify_slot false PASS) | NOT YET PROBED | |
| 11 | Storage keys (path traversal into read/write/read_object/append_object) | NOT YET PROBED | |

## Findings detail

(populated as categories are probed)
