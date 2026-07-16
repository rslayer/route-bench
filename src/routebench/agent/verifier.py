"""Verifier — checks that every claim maps to a structured finding.

Extracts numeric tokens and entity references from generated prose,
verifies them against the slot's source data, and optionally uses
an LLM as a secondary judge.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import structlog

from routebench.agent.client import LLMClient
from routebench.report.prose_slots import ProseSlot

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Comma-grouped numbers are matched first so "1,234" is one token worth 1234,
# never the pair (1, 234) — which would let a wrong claim verify against two
# unrelated source values.
_COMMA_GROUPED = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
_PLAIN = r"\d+(?:\.\d+)?"
_NUMBER_RE = re.compile(rf"\b(?:{_COMMA_GROUPED}|{_PLAIN})\b")
_ROUTE_ID_RE = re.compile(r"\b(R[-_]\w+)\b")
_FINDING_ID_RE = re.compile(r"\b([0-9a-f]{8,16})\b")

# An ordered-list marker ("1." / "2)") at the start of a line is formatting,
# not a claim about the fleet.
_LIST_MARKER_RE = re.compile(r"(?:^|\n)[ \t]*(\d+)[.)](?=\s)")

# A claim verifies within 5% of the source, with an absolute floor so that
# small sources (and zero) stay checkable. Prose rounding survives this;
# a genuinely different number does not.
_RELATIVE_TOLERANCE = 0.05
_ABSOLUTE_FLOOR = 0.05

# Values that stay exempt from checking, and only as bare list markers.
_LIST_MARKER_EXEMPT = frozenset({0.0, 1.0})

SlotStatus = Literal["verified", "regenerated", "fallback"]


class VerificationResult:
    """Result of verifying a single prose slot.

    `status` records how the final prose was produced. `passed` is retained for
    backward compatibility and means "checked and clean" — a deterministic
    fallback is safe to publish but is not a verified LLM claim, so it reports
    passed=False with status="fallback" rather than passing silently.
    """

    def __init__(
        self,
        slot_id: str,
        passed: bool,
        issues: list[str] | None = None,
        status: SlotStatus = "verified",
    ) -> None:
        self.slot_id = slot_id
        self.passed = passed
        self.issues = issues or []
        self.status = status


def _extract_numbers_from_data(data: dict[str, Any]) -> set[float]:
    """Recursively extract all numeric values from input data."""
    numbers: set[float] = set()

    def _walk(obj: object) -> None:
        if isinstance(obj, (int, float)):
            numbers.add(float(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return numbers


def _extract_collection_counts(data: dict[str, Any]) -> set[float]:
    """Collect the length of every list in the source data.

    Prose legitimately counts referenced collections — "3 of the 12 routes"
    where the finding references 12 route_ids. The 12 appears nowhere as a
    value, only as a length, so without this the claim would be flagged.
    Deliberately limited to collection sizes; no general arithmetic.
    """
    counts: set[float] = set()

    def _walk(obj: object) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            counts.add(float(len(obj)))
            for item in obj:
                _walk(item)

    _walk(data)
    return counts


def _list_marker_spans(prose: str) -> list[tuple[int, int]]:
    """Character spans of ordered-list markers, which are formatting not claims."""
    return [m.span(1) for m in _LIST_MARKER_RE.finditer(prose)]


def _mask_identifiers(prose: str, slot: ProseSlot) -> str:
    """Blank out identifiers so their digits are not read as numeric claims.

    "R-001" contains a word-boundary "001", which would otherwise be checked as
    the number 1 and flagged on every route mention. Identifiers are verified
    separately (route IDs against the source, finding IDs as required
    references), so their digits carry no quantitative claim.

    Each identifier is replaced by spaces of equal length, keeping every offset
    stable so list-marker spans still line up.
    """

    def _blank(match: re.Match[str]) -> str:
        return " " * len(match.group())

    masked = _ROUTE_ID_RE.sub(_blank, prose)

    known_ids = set(slot.required_references) | _extract_finding_ids_from_data(slot.input_data)
    for identifier in known_ids:
        if identifier:
            masked = masked.replace(identifier, " " * len(identifier))
    return masked


def _within_tolerance(claim: float, source: float) -> bool:
    """True when `claim` is within 5% of `source` (with an absolute floor)."""
    return abs(claim - source) <= max(_RELATIVE_TOLERANCE * abs(source), _ABSOLUTE_FLOOR)


def _extract_route_ids_from_data(data: dict[str, Any]) -> set[str]:
    """Recursively extract all route_id values from input data."""
    route_ids: set[str] = set()

    def _walk(obj: object) -> None:
        if isinstance(obj, dict):
            if "route_id" in obj:
                route_ids.add(str(obj["route_id"]))
            if "route_ids" in obj and isinstance(obj["route_ids"], list):
                for rid in obj["route_ids"]:
                    route_ids.add(str(rid))
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return route_ids


def _extract_finding_ids_from_data(data: dict[str, Any]) -> set[str]:
    """Recursively extract all finding_id values from input data."""
    finding_ids: set[str] = set()

    def _walk(obj: object) -> None:
        if isinstance(obj, dict):
            if "finding_id" in obj:
                finding_ids.add(str(obj["finding_id"]))
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return finding_ids


def verify_slot(
    prose: str,
    slot: ProseSlot,
) -> VerificationResult:
    """Verify a prose slot against its source data.

    Checks:
    1. Numeric tokens in prose appear in source data (within relative tolerance),
       or equal the size of a referenced collection.
    2. Route IDs in prose appear in source data.
    3. Required finding references are mentioned.

    Only 0 and 1 are ever skipped, and only as ordered-list markers. 100 is
    checked like any other value: percentages near 100 are exactly the claims
    worth checking. A false flag is cheap — the slot regenerates once and then
    falls back deterministically — while a false pass ships an invented number.
    """
    issues: list[str] = []

    # Extract data values
    source_numbers = _extract_numbers_from_data(slot.input_data)
    source_route_ids = _extract_route_ids_from_data(slot.input_data)
    collection_counts = _extract_collection_counts(slot.input_data)

    # Check numbers in prose, ignoring digits that belong to identifiers
    masked = _mask_identifiers(prose, slot)
    marker_spans = _list_marker_spans(masked)
    for match in _NUMBER_RE.finditer(masked):
        raw = match.group()
        num = float(raw.replace(",", ""))

        if num in _LIST_MARKER_EXEMPT and match.span() in marker_spans:
            continue

        matched = any(_within_tolerance(num, src) for src in source_numbers)
        if not matched and num.is_integer():
            matched = num in collection_counts
        if not matched:
            issues.append(f"Number {raw} not found in source data")

    # Check route IDs in prose
    prose_route_ids = set(_ROUTE_ID_RE.findall(prose))
    for rid in prose_route_ids:
        if rid not in source_route_ids:
            issues.append(f"Route ID {rid} not found in source data")

    # Check required references
    for required_id in slot.required_references:
        if required_id not in prose:
            issues.append(f"Required reference {required_id} not mentioned in prose")

    passed = len(issues) == 0
    return VerificationResult(
        slot_id=slot.slot_id,
        passed=passed,
        issues=issues,
    )


def summarize_statuses(results: dict[str, VerificationResult]) -> dict[str, int]:
    """Count slots by status, for the report footer.

    Every status is reported separately so a deterministic fallback is never
    folded into the verified count.
    """
    counts: dict[str, int] = {"verified": 0, "regenerated": 0, "fallback": 0}
    for result in results.values():
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


class Verifier:
    """Full verification pipeline with optional LLM judge."""

    def __init__(
        self,
        client: LLMClient | None = None,
        use_llm_judge: bool = False,
    ) -> None:
        self._client = client
        self._use_llm_judge = use_llm_judge and client is not None

    def verify_all(
        self,
        filled_slots: dict[str, str],
        slots: list[ProseSlot],
    ) -> dict[str, VerificationResult]:
        """Verify all filled prose slots."""
        slot_map = {s.slot_id: s for s in slots}
        results: dict[str, VerificationResult] = {}

        for slot_id, prose in filled_slots.items():
            if slot_id not in slot_map:
                results[slot_id] = VerificationResult(
                    slot_id=slot_id,
                    passed=False,
                    issues=[f"No slot definition found for {slot_id}"],
                )
                continue

            slot = slot_map[slot_id]
            result = verify_slot(prose, slot)

            if result.passed and self._use_llm_judge and self._client:
                llm_result = self._llm_judge(prose, slot)
                if not llm_result:
                    result = VerificationResult(
                        slot_id=slot_id,
                        passed=False,
                        issues=["LLM judge flagged inaccuracy"],
                    )

            results[slot_id] = result

        return results

    def verify_and_regenerate(
        self,
        filled_slots: dict[str, str],
        slots: list[ProseSlot],
        writer_fn: Any,
    ) -> tuple[dict[str, str], dict[str, VerificationResult]]:
        """Verify all slots, regenerate failed ones once.

        writer_fn should be a callable that takes a ProseSlot and returns str.
        Falls back to a deterministic template on second failure.
        """
        slot_map = {s.slot_id: s for s in slots}
        results = self.verify_all(filled_slots, slots)
        final_prose = dict(filled_slots)

        for slot_id, result in results.items():
            if result.passed:
                continue

            slot = slot_map.get(slot_id)
            if slot is None:
                continue

            # Retry once
            try:
                new_prose: str = writer_fn(slot)
                retry_result = verify_slot(new_prose, slot)

                if retry_result.passed:
                    final_prose[slot_id] = new_prose
                    retry_result.status = "regenerated"
                    results[slot_id] = retry_result
                else:
                    # Fall back to deterministic template. The prose is safe to
                    # publish, but it is not a verified LLM claim — passed=False
                    # keeps it out of the verified count.
                    fallback = self._deterministic_fallback(slot)
                    final_prose[slot_id] = fallback
                    results[slot_id] = VerificationResult(
                        slot_id=slot_id,
                        passed=False,
                        issues=["Used deterministic fallback", *retry_result.issues],
                        status="fallback",
                    )
            except Exception:
                logger.exception("regeneration_error", slot_id=slot_id)
                fallback = self._deterministic_fallback(slot)
                final_prose[slot_id] = fallback
                results[slot_id] = VerificationResult(
                    slot_id=slot_id,
                    passed=False,
                    issues=["Used deterministic fallback after error"],
                    status="fallback",
                )

        return final_prose, results

    def _llm_judge(self, prose: str, slot: ProseSlot) -> bool:
        """Use LLM as judge to verify prose accuracy."""
        if not self._client:
            return True

        from pathlib import Path

        prompt_path = Path(__file__).parent / "prompts" / "verifier.md"
        system = prompt_path.read_text()

        data_str = json.dumps(slot.input_data, indent=2, default=str)
        user_msg = f"Source data:\n{data_str}\n\nProse:\n{prose}"

        response = self._client.generate(
            messages=[{"role": "user", "content": user_msg}],
            system=system,
            slot_id=f"verify_{slot.slot_id}",
            max_tokens=256,
        )

        return response.text.strip().upper().startswith("PASS")

    def _deterministic_fallback(self, slot: ProseSlot) -> str:
        """Generate a deterministic template-based fallback."""
        data = slot.input_data

        if slot.slot_type == "executive_summary":
            fm = data.get("fleet_metrics", {})
            return (
                f"Fleet analysis covers {fm.get('total_routes', 'N/A')} routes "
                f"with {fm.get('total_stops', 'N/A')} stops, "
                f"totaling {fm.get('total_distance_miles', 'N/A'):.1f} miles."
            )
        if slot.slot_type == "finding_explanation":
            f = data.get("finding", {})
            return (
                f"Finding {f.get('finding_id', 'N/A')}: "
                f"{f.get('title', 'N/A')} "
                f"(severity: {f.get('severity', 'N/A')})."
            )
        if slot.slot_type == "investigation_priorities":
            return "Refer to the findings list for investigation priorities."

        return f"[{slot.slot_type}] — see structured data for details."
