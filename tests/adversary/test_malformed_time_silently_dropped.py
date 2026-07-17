"""Defect: an unparseable time/datetime string in an optional CSV column is
silently discarded by `validate_csv` with no error, no warning, and no
`defaults_applied` entry — indistinguishable from the column being absent.

Impact: every other malformed-value path in `validate_csv`
(routebench/core/validation.py) surfaces something to the caller: numeric
columns that fail to cast raise `INVALID_TYPE`, out-of-range coordinates
raise `OUT_OF_RANGE`, and legitimately-missing optional fields that get a
default recorded in `ValidationReport.defaults_applied`. Garbage in
`planned_arrival_time` / `time_window_start` / `time_window_end` takes none
of those paths: `_parse_datetime`/`_parse_time` catch `ValueError` and return
`None` with no trace left anywhere. A corrupted export (a broken date format,
a stray character, a copy-paste error) is silently treated as "this stop has
no arrival time" — the row still validates cleanly (`is_valid=True`,
`errors=[]`, `warnings=[]`) and the caller has no way to discover that their
data was ignored rather than parsed.

Root cause: `_parse_datetime` and `_parse_time` in
routebench/core/validation.py swallow every parse failure identically to a
genuinely-empty string, instead of distinguishing "field was blank" (fine,
apply the default) from "field had content that could not be parsed"
(should be a `ValidationWarning` at minimum, matching the pattern already
used elsewhere in this same function for every other applied default).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from routebench.core.validation import validate_csv


def test_garbage_time_string_leaves_no_trace() -> None:
    csv_bytes = (
        b"route_id,stop_sequence,latitude,longitude,planned_arrival_time\n"
        b"R-001,0,32.825,-96.775,\n"
        b"R-001,1,32.830,-96.770,not-a-real-time-at-all!!\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(csv_bytes)
        path = Path(f.name)

    try:
        fleet, report = validate_csv(path)
    finally:
        path.unlink(missing_ok=True)

    assert fleet is not None
    stop = fleet.routes[0].stops[0]

    # The upload said "not-a-real-time-at-all!!" — a clearly garbage value,
    # not an empty one. The system silently converted it to "no time
    # provided" with zero acknowledgement in the validation report.
    assert stop.planned_arrival_time is None  # confirms the value was dropped

    garbage_was_surfaced = bool(report.warnings) or bool(report.errors)
    assert garbage_was_surfaced, (
        "an unparseable planned_arrival_time was silently discarded with no "
        "warning or error recorded anywhere in the ValidationReport "
        f"(errors={report.errors!r}, warnings={report.warnings!r}, "
        f"defaults_applied={report.defaults_applied!r})"
    )
