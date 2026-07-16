"""Phase 10.5 Part A: verifier robustness.

The verifier is the firewall between structured findings and prose. Its failure
mode that matters is the false pass — an invented number reaching the reader.
These tests pin the gaps that allowed one.
"""

from __future__ import annotations

from typing import Any

from routebench.agent.verifier import (
    VerificationResult,
    Verifier,
    summarize_statuses,
    verify_slot,
)
from routebench.report.prose_slots import ProseSlot


def _slot(data: dict[str, Any], refs: list[str] | None = None) -> ProseSlot:
    return ProseSlot(
        slot_id="test_slot",
        slot_type="executive_summary",
        prompt_template="writer_executive_summary",
        input_data=data,
        word_budget=100,
        required_references=refs or [],
    )


class TestCommaFormattedNumbers:
    """A comma-grouped number is one token, not several."""

    def test_comma_number_verifies_against_source(self) -> None:
        assert verify_slot("1,240 miles", _slot({"total": 1240.0})).passed

    def test_comma_decimal_verifies(self) -> None:
        assert verify_slot("1,234.5 miles", _slot({"total": 1234.5})).passed

    def test_comma_number_not_split_into_parts(self) -> None:
        """12,345 must not verify by matching 12 and 345 separately."""
        result = verify_slot("12,345 miles", _slot({"a": 12.0, "b": 345.0}))
        assert not result.passed
        assert any("12,345" in issue for issue in result.issues)

    def test_comma_number_still_flagged_when_invented(self) -> None:
        assert not verify_slot("9,999 miles", _slot({"total": 1240.0})).passed


class TestRelativeTolerance:
    """Tolerance scales with the source, so big numbers can round and small ones cannot."""

    def test_prose_rounding_verifies(self) -> None:
        assert verify_slot("87 percent", _slot({"v": 87.3})).passed

    def test_small_value_drift_fails(self) -> None:
        """0.6 vs 0.2 is a threefold error, well outside the floor."""
        assert not verify_slot("0.6 index", _slot({"v": 0.2})).passed

    def test_five_percent_drift_verifies(self) -> None:
        assert verify_slot("105 miles", _slot({"v": 100.0})).passed

    def test_ten_percent_drift_fails(self) -> None:
        assert not verify_slot("110 miles", _slot({"v": 100.0})).passed

    def test_absolute_floor_allows_tiny_rounding(self) -> None:
        """Near zero the relative band collapses, so the floor carries it."""
        assert verify_slot("0.02 hours", _slot({"v": 0.0})).passed


class TestSkipList:
    """0 and 1 survive only as list markers; 100 is checked like anything else."""

    def test_100_is_checked(self) -> None:
        assert not verify_slot("100 percent utilization", _slot({"v": 42.0})).passed

    def test_100_verifies_when_real(self) -> None:
        assert verify_slot("100 percent utilization", _slot({"v": 100.0})).passed

    def test_near_100_is_checked(self) -> None:
        assert not verify_slot("99 percent", _slot({"v": 42.0})).passed

    def test_list_markers_are_skipped(self) -> None:
        prose = "1. Resequence R-001\n2. Review R-002"
        assert verify_slot(prose, _slot({"route_ids": ["R-001", "R-002"]})).passed

    def test_paren_list_markers_are_skipped(self) -> None:
        prose = "1) Resequence R-001\n2) Review R-002"
        assert verify_slot(prose, _slot({"route_ids": ["R-001", "R-002"]})).passed

    def test_bare_one_is_checked_outside_a_list(self) -> None:
        """The old blanket exemption let an invented count through here."""
        assert not verify_slot("1 route exceeds the cap", _slot({"total_routes": 6.0})).passed

    def test_bare_zero_is_checked_outside_a_list(self) -> None:
        assert not verify_slot("0 routes exceed the cap", _slot({"total_routes": 6.0})).passed


class TestIdentifierMasking:
    """Digits inside identifiers are not numeric claims."""

    def test_route_id_digits_do_not_flag(self) -> None:
        """R-001 contains a word-boundary 001; it must not read as the number 1."""
        result = verify_slot(
            "Route R-001 wastes 12.5 miles", _slot({"route_ids": ["R-001"], "v": 12.5})
        )
        assert result.passed, result.issues

    def test_finding_id_digits_do_not_flag(self) -> None:
        result = verify_slot(
            "Finding a1b2c3d4e5f6 concerns R-001",
            _slot({"finding_id": "a1b2c3d4e5f6", "route_ids": ["R-001"]}, refs=["a1b2c3d4e5f6"]),
        )
        assert result.passed, result.issues

    def test_masking_does_not_hide_real_claims(self) -> None:
        assert not verify_slot(
            "Route R-001 wastes 99.9 miles", _slot({"route_ids": ["R-001"], "v": 12.5})
        ).passed

    def test_unknown_route_id_still_flagged(self) -> None:
        assert not verify_slot("Route R-404 is late", _slot({"route_ids": ["R-001"]})).passed


class TestCollectionCounts:
    """A count of referenced items is a legitimate claim."""

    def test_collection_length_verifies(self) -> None:
        data = {"route_ids": [f"R-{i:03d}" for i in range(1, 13)], "flagged": 3.0}
        assert verify_slot("3 of the 12 routes are flagged", _slot(data)).passed

    def test_wrong_collection_count_fails(self) -> None:
        data = {"route_ids": [f"R-{i:03d}" for i in range(1, 13)]}
        assert not verify_slot("40 routes were analyzed", _slot(data)).passed

    def test_non_integer_does_not_match_a_count(self) -> None:
        """Only integers can be collection counts; 12.7 is a measurement."""
        data = {"route_ids": [f"R-{i:03d}" for i in range(1, 13)]}
        assert not verify_slot("12.7 miles wasted", _slot(data)).passed


class TestSlotStatus:
    """Fallbacks must be distinguishable from verified prose."""

    def test_clean_slot_is_verified(self) -> None:
        result = verify_slot("87 percent", _slot({"v": 87.3}))
        assert result.status == "verified"
        assert result.passed

    def test_retry_success_is_regenerated(self) -> None:
        slot = _slot({"v": 87.3})
        verifier = Verifier()
        final, results = verifier.verify_and_regenerate(
            {"test_slot": "999 percent"}, [slot], lambda s: "87 percent"
        )
        assert results["test_slot"].status == "regenerated"
        assert results["test_slot"].passed
        assert final["test_slot"] == "87 percent"

    def test_persistent_failure_is_fallback_and_not_passed(self) -> None:
        """A fallback is safe to publish but is not a verified claim."""
        slot = _slot(
            {"fleet_metrics": {"total_routes": 3, "total_stops": 9, "total_distance_miles": 10.0}}
        )
        verifier = Verifier()
        final, results = verifier.verify_and_regenerate(
            {"test_slot": "999 percent"}, [slot], lambda s: "888 percent"
        )
        assert results["test_slot"].status == "fallback"
        assert not results["test_slot"].passed, "a fallback must not report passed=True"
        assert final["test_slot"] != "888 percent"

    def test_writer_error_is_fallback(self) -> None:
        slot = _slot(
            {"fleet_metrics": {"total_routes": 3, "total_stops": 9, "total_distance_miles": 10.0}}
        )

        def _boom(s: ProseSlot) -> str:
            raise RuntimeError("writer down")

        verifier = Verifier()
        _final, results = verifier.verify_and_regenerate(
            {"test_slot": "999 percent"}, [slot], _boom
        )
        assert results["test_slot"].status == "fallback"
        assert not results["test_slot"].passed


class TestSummarizeStatuses:
    """The footer counts each status separately."""

    def test_counts_each_status(self) -> None:
        results = {
            "a": VerificationResult("a", True, status="verified"),
            "b": VerificationResult("b", True, status="verified"),
            "c": VerificationResult("c", True, status="regenerated"),
            "d": VerificationResult("d", False, status="fallback"),
        }
        assert summarize_statuses(results) == {"verified": 2, "regenerated": 1, "fallback": 1}

    def test_empty_is_all_zero(self) -> None:
        assert summarize_statuses({}) == {"verified": 0, "regenerated": 0, "fallback": 0}
