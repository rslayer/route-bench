"""Defect: a fractional `stop_sequence` (e.g. "0.9", "1.9") is silently
truncated to an integer and accepted as a fully valid fleet, instead of
being rejected as an invalid stop_sequence.

Impact: `stop_sequence` is the field that identifies which row is the depot
and defines route order. `validate_csv` casts it with
`pl.col("stop_sequence").cast(pl.Int64)`. When the column's inferred dtype
is float (which happens the moment any row has a decimal value), polars'
float -> Int64 cast truncates toward zero rather than raising, so "0.9"
becomes 0 and "1.9" becomes 1. The CSV is then accepted as `is_valid=True`
with no error, no warning, and no record that the original values were
non-integers, even though stop_sequence is a required identifying column,
not a free-form number.

Root cause: `core/validation.py`'s stop_sequence cast
(`pl.col("stop_sequence").cast(pl.Int64)`) only guards against a cast that
polars refuses outright (e.g. a non-numeric string); it does not check that
the *pre-cast* value was already an integer, so a truncating numeric cast
is indistinguishable from a clean one.
"""

from __future__ import annotations

from pathlib import Path

from routebench.core.validation import validate_csv

FRACTIONAL_STOP_SEQUENCE_CSV = b"""route_id,stop_sequence,latitude,longitude
R-001,0.9,32.825,-96.775
R-001,1.9,32.830,-96.770
"""


def test_fractional_stop_sequence_is_rejected(tmp_path: Path) -> None:
    csv_path = tmp_path / "fractional_stop_sequence.csv"
    csv_path.write_bytes(FRACTIONAL_STOP_SEQUENCE_CSV)

    fleet, report = validate_csv(csv_path)

    # Truncating "0.9" -> 0 and "1.9" -> 1 currently makes this look like a
    # perfectly normal two-row route (depot + one stop) and validate_csv
    # returns is_valid=True with zero errors. The input was never actually a
    # valid depot/stop-sequence 0/1 pair — that was manufactured by the cast.
    assert fleet is None, (
        "fractional stop_sequence values were silently truncated to integers "
        "and accepted as a valid fleet"
    )
    assert any(e.column == "stop_sequence" for e in report.errors)
