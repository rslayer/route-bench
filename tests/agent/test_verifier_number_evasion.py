"""Regression guards: two ways a fabricated number slipped past verify_slot.

Both were found by robustness run 3 and both defeat the Phase 10.5 verifier
hardening. Promoted here from tests/adversary/ as the fix landed, and written
to assert the behaviour we want — the fabricated number is FLAGGED — plus the
guard cases that must keep passing so the fix cannot quietly over-correct.

See ROBUSTNESS.md.
"""

from __future__ import annotations

from routebench.agent.verifier import verify_slot
from routebench.report.prose_slots import ProseSlot


class TestUnitSuffixNumberEvasion:
    """`_NUMBER_RE`'s trailing `\\b` skipped a digit run glued to a unit letter:
    "4700min" sits between two word chars, so no substring satisfied the
    boundary and the whole figure was never extracted."""

    def _slot(self) -> ProseSlot:
        return ProseSlot(
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

    def test_bare_integer_with_unit_suffix_is_flagged(self) -> None:
        prose = (
            "Drivers were delayed by 4700min across the fleet this week, "
            "exceeding capacity by 8500lbs on average and adding 950pct over "
            "the theoretical optimum."
        )
        result = verify_slot(prose, self._slot())
        assert not result.passed, (
            f"fabricated unit-suffixed numbers evaded extraction: {result.issues!r}"
        )
        # 8500 and 950 are genuinely out of tolerance and must be flagged; each
        # is now extracted despite the glued suffix. (4700 is deliberately not
        # asserted: it happens to fall within 5% of total_distance_miles=4821.3,
        # so it verifies legitimately once extracted — which is the point. The
        # bug was that the suffix skipped extraction entirely.)
        joined = " ".join(result.issues)
        assert "8500" in joined and "950" in joined

    def test_legit_unit_suffixed_number_still_passes(self) -> None:
        """The fix must extract the glued number, not blanket-reject the shape:
        a suffixed number that IS in the source data must still verify."""
        prose = "The fleet covered 4821miles across 40 stops."
        result = verify_slot(prose, self._slot())
        assert result.passed, f"a source-backed unit-suffixed number was flagged: {result.issues!r}"


class TestIdentifierSubstringEvasion:
    """`_mask_identifiers` blanked a finding_id via plain str.replace, so an id
    that was the PREFIX of a larger fabricated number got partially erased —
    "12345678" inside "$123456789" left a lone "9" that matched a real value."""

    def _slot(self) -> ProseSlot:
        return ProseSlot(
            slot_id="finding_12345678",
            slot_type="finding_explanation",
            prompt_template="writer_finding_explanation",
            input_data={
                "finding": {"finding_id": "12345678", "title": "Underutilized route"},
                "fleet_summary": {"total_routes": 9},
            },
            word_budget=150,
            required_references=["12345678"],
        )

    def test_number_containing_masked_identifier_as_prefix_is_flagged(self) -> None:
        prose = (
            "Finding 12345678 indicates the fleet lost $123456789 in wasted "
            "mileage this quarter across 9 routes."
        )
        result = verify_slot(prose, self._slot())
        assert not result.passed, (
            f"a fabricated $123,456,789 survived because its prefix matched a "
            f"masked finding_id: {result.issues!r}"
        )
        assert any("123456789" in i for i in result.issues)

    def test_standalone_identifier_is_still_masked(self) -> None:
        """The guard: a finding_id used as a plain reference must NOT be read as
        a numeric claim. Only the required reference is present, no other
        numbers, so the slot must verify clean."""
        prose = "Finding 12345678 documents an underutilized route in the fleet."
        result = verify_slot(prose, self._slot())
        assert result.passed, (
            f"a standalone finding_id reference was mis-read as a number: {result.issues!r}"
        )
