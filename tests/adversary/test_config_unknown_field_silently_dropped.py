"""Defect: an unrecognized/misspelled key anywhere in the `config` JSON
(e.g. `work_rules.mx_shift_hours` instead of `max_shift_hours`) is silently
ignored — pydantic's default `extra="ignore"` behaviour — and the field
silently falls back to its default, with no error and no warning anywhere
in the response.

Impact: `POST /sessions` builds `AnalysisConfig(**config_data)` directly
from the caller's JSON (`app/api/routes.py::create_session`). A client that
believes it set `max_shift_hours: 1` (say, tightening a shift cap to catch
overrun findings) but sent the misspelled `mx_shift_hours` gets a 202
Accepted and a report silently graded against the *default* 12-hour shift
cap instead. There is no way for the caller to tell, from the response,
that their constraint was never applied — the config that gets echoed back
via `config.json`/`AnalysisConfig(**data).model_dump()` looks completely
normal because the typo'd key simply isn't there.

Root cause: none of `AnalysisConfig`, `WorkRules`, `ServiceTimeModel`,
`TrafficProfile`, or `TrafficBand` set `model_config = ConfigDict(extra=
"forbid")`, so pydantic v2's default (`extra="ignore"`) applies throughout
the config surface that `POST /sessions` builds directly from user JSON.
"""

from __future__ import annotations

from routebench.core.config import AnalysisConfig


def test_misspelled_work_rules_field_is_rejected_not_ignored() -> None:
    """A typo'd field name inside work_rules must not be silently dropped in
    favor of the default — the caller asked for a specific shift cap and
    never got it, with no error to say so."""
    intended_max_shift_hours = 1.0

    config = AnalysisConfig(work_rules={"mx_shift_hours": intended_max_shift_hours})

    assert config.work_rules.max_shift_hours == intended_max_shift_hours, (
        "the misspelled 'mx_shift_hours' key was silently dropped; "
        f"max_shift_hours quietly fell back to the default "
        f"({config.work_rules.max_shift_hours}) instead of raising a "
        "validation error for the unrecognized field"
    )


def test_misspelled_top_level_field_is_rejected_not_ignored() -> None:
    intended_threshold = 42.0

    config = AnalysisConfig(sequencing_threshod=intended_threshold)  # noqa: typo intentional

    assert config.sequencing_threshold == intended_threshold, (
        "the misspelled top-level 'sequencing_threshod' key was silently "
        "dropped instead of raising a validation error"
    )
