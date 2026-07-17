"""Defect: a fabricated number that has a known identifier as a *substring*
(rather than as the whole token) gets partially erased by identifier masking,
letting the surviving remainder coincidentally match real source data — so
`verify_slot` reports a clean PASS for a wholly invented figure.

Impact: same class as test_verifier_unit_suffix_number_evasion.py — a false
PASS on a fabricated claim, which is precisely what verification exists to
catch. Here the fabricated claim is a large, attention-grabbing figure (a
dollar amount) rather than a small one, so the blast radius on a real report
is worse: the invented number is exactly the kind of headline claim a reader
would trust.

Root cause: `_mask_identifiers` in routebench/agent/verifier.py blanks known
identifiers with `masked.replace(identifier, " " * len(identifier))` — a
plain substring replace, not a whole-token match. `required_references` (and
finding_ids extracted from `slot.input_data`) are used as `identifier` here.
When an identifier is itself a run of digits (finding_id is a 16-hex-char
sha256 prefix, `hashlib...hexdigest()[:16]`, which can be all-digit) and it
appears as a *prefix* of a longer fabricated number in the prose, `.replace`
strips only the identifier-length portion, leaving the trailing digits of
the fabricated number as an isolated, shorter, unrelated-looking token. If
that leftover token happens to equal any real source value (trivially
engineered here, but also plausible by chance with short leftovers), the
whole fabricated number passes unnoticed — the number that was actually
checked (`9`) is not the number that was actually claimed (`123456789`).
"""

from __future__ import annotations

from routebench.agent.verifier import verify_slot
from routebench.report.prose_slots import ProseSlot


def test_number_containing_masked_identifier_as_prefix_evades_check() -> None:
    slot = ProseSlot(
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

    # $123,456,789 appears nowhere in the source data — it is entirely
    # invented. It is shaped so its leading 8 digits exactly match the
    # finding_id that _mask_identifiers blanks out, leaving only a trailing
    # "9" that coincidentally equals total_routes=9 in the source data.
    prose = (
        "Finding 12345678 indicates the fleet lost $123456789 in wasted "
        "mileage this quarter across 9 routes."
    )

    result = verify_slot(prose, slot)

    assert not result.passed, (
        "verify_slot passed prose containing a fabricated $123,456,789 "
        "figure with zero issues — identifier-substring masking erased the "
        f"claim before it could be checked: {result.issues!r}"
    )
