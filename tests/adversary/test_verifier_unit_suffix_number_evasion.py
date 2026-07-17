"""Defect: fabricated numbers immediately suffixed by a unit-like letter (no
space, no decimal point) evade `verify_slot`'s number-extraction entirely,
producing a clean PASS for prose containing invented figures.

Impact: this is the exact failure mode `verify_slot` exists to prevent — an
LLM-written claim with a number that appears nowhere in the source data is
reported as `passed=True, issues=[]`, so it ships in the report unflagged and
never gets a regeneration attempt or a deterministic fallback.

Root cause: `_NUMBER_RE` in routebench/agent/verifier.py requires a `\\b` word
boundary at both ends of the digit run (`_PLAIN = r"\\d+(?:\\.\\d+)?"`). A
boundary exists only at a transition between a word character and a
non-word character. When a bare integer is followed immediately by a letter
("4700min", "8500lbs", "950pct" — no space, no decimal point), every
candidate end position sits between two word characters (digit-digit or
digit-letter), so no substring of the run ever satisfies the trailing `\\b`.
The regex engine cannot match *any* prefix of the number, and the whole
token is silently skipped rather than flagged as an unrecognized claim.

(A decimal-pointed number like "45.2k" partially resists this because the
"." gives the regex a non-word character to backtrack to, so "45" alone gets
extracted and correctly flagged — but a plain integer suffix has no such
escape hatch.)
"""

from __future__ import annotations

from routebench.agent.verifier import verify_slot
from routebench.report.prose_slots import ProseSlot


def test_bare_integer_with_unit_suffix_evades_number_check() -> None:
    slot = ProseSlot(
        slot_id="executive_summary",
        slot_type="executive_summary",
        prompt_template="writer_executive_summary",
        input_data={
            "fleet_metrics": {
                "total_distance_miles": 4821.3,
                "total_stops": 40,
                "total_time_hours": 120.0,
            }
        },
        word_budget=100,
    )

    # None of 4700, 8500, or 950 appear anywhere in the source data (nor are
    # they within 5% of any source value) — every one is fabricated.
    prose = (
        "Drivers were delayed by 4700min across the fleet this week, "
        "exceeding capacity by 8500lbs on average and adding 950pct over "
        "the theoretical optimum."
    )

    result = verify_slot(prose, slot)

    assert not result.passed, (
        "verify_slot passed prose containing three fabricated numbers "
        "(4700min, 8500lbs, 950pct) with zero issues raised — the unit "
        f"suffix let all three evade extraction entirely: {result.issues!r}"
    )
