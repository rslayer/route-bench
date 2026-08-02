"""Property-based fuzzing of the CSV validator — the largest attack surface.

`validate_csv` is the front door for every upload. Its contract is that it
*never* raises: whatever bytes arrive, it returns ``(Fleet | None,
ValidationReport)`` — a typed rejection, not a 500. Prior robustness runs found
NaN coordinates, fractional stop sequences and negative service times crashing
it one payload at a time; this exercises the same surface at volume so the next
such crash is caught before it ships.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from routebench.core.config import AnalysisConfig
from routebench.core.validation import ValidationReport, validate_csv

_CONFIG = AnalysisConfig()

# Adversarial cell values: numeric edge cases, injection-shaped text, unicode,
# control chars, delimiter/quote confusion, huge fields.
_NASTY_CELLS = st.one_of(
    st.sampled_from(
        [
            "",
            " ",
            "0",
            "-0",
            "nan",
            "NaN",
            "inf",
            "-inf",
            "1e309",
            "1e-309",
            "90.0000001",
            "-180.1",
            "0,0",
            '"',
            "'",
            ",",
            "\n",
            "\r\n",
            "\t",
            "1.5",
            "9" * 400,
            "٩",
            "\x00",
            "=cmd|' /C calc'!A0",
            "<script>x</script>",
            "../../etc/passwd",
            "true",
            "False",
            "1_000",
            "0x10",
            "+",
            "NULL",
        ]
    ),
    st.text(max_size=40),
    st.integers().map(str),
    st.floats(allow_nan=True, allow_infinity=True).map(repr),
)


def _rows(strategy):
    return st.lists(st.lists(strategy, min_size=1, max_size=6), min_size=0, max_size=12)


def _assert_typed_result(data: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(data)
        path = Path(f.name)
    try:
        result = validate_csv(path, _CONFIG)
    finally:
        path.unlink(missing_ok=True)
    assert isinstance(result, tuple) and len(result) == 2
    fleet, report = result
    assert isinstance(report, ValidationReport)
    # a rejected upload must surface at least one error, never a silent None+clean
    if fleet is None:
        assert report.errors


@settings(max_examples=200, deadline=None)
@given(rows=_rows(_NASTY_CELLS))
def test_structured_csv_never_crashes(rows):
    # Real-looking header, adversarial body (ragged rows included).
    header = "route_id,stop_sequence,latitude,longitude,service_time_minutes"
    body = "\n".join(",".join(cells) for cells in rows)
    _assert_typed_result(f"{header}\n{body}\n".encode())


@settings(max_examples=150, deadline=None)
@given(header=st.text(max_size=60), rows=_rows(_NASTY_CELLS))
def test_arbitrary_header_never_crashes(header, rows):
    # Fuzz the header too: missing/renamed/duplicate/garbage columns.
    body = "\n".join(",".join(cells) for cells in rows)
    _assert_typed_result(f"{header}\n{body}\n".encode())


@settings(max_examples=150, deadline=None)
@given(blob=st.binary(max_size=4096))
def test_arbitrary_bytes_never_crash(blob):
    # Not even valid text — encoding errors, BOMs, NUL bytes, binary garbage.
    _assert_typed_result(blob)
