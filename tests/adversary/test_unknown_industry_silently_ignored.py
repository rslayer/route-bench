"""Defect: an unrecognized `industry` config key is silently accepted.

`AnalysisConfig.traffic` validates a named-profile string against
`NAMED_TRAFFIC_PROFILES` and raises a clear 422 for an unknown name (see
`_resolve_named_profile` in core/config.py). `AnalysisConfig.industry` has no
equivalent check — it is a bare `str | None` — so a typo'd or fabricated
industry key sails through config validation with a 202, not a 422.

Downstream, `core.industry.get_profile(key)` is a plain `dict.get`, so the
bogus key resolves to `None`, and the pipeline silently runs as if no
industry was selected at all:

  * no industry-specific `grading_weights` blend is applied
  * `analysis/diagnosis/service_sanity.py`'s implausible-service-time check
    is skipped entirely ("No industry chosen -> no band to judge against")

A caller who believes they selected e.g. "courier" but typo'd "courrier"
gets a materially different, unflagged analysis with no error or warning
anywhere in the response or the validation report. This is inconsistent with
how every other named-preset-style field in this config (traffic profiles,
work rules, band shapes) is validated — those all reject an unrecognized or
malformed value outright, which is the behavior this test expects but does
not get.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from routebench.core.config import AnalysisConfig
from routebench.core.industry import INDUSTRY_PROFILES


def test_unknown_industry_key_is_rejected_at_construction() -> None:
    """An unrecognized industry key should fail config validation the same
    way an unrecognized traffic profile name does.

    It currently does not: `AnalysisConfig(industry=...)` accepts any string
    and the bogus value is only discovered (as a silent no-op) deep inside
    the pipeline, if at all.
    """
    assert "totally-made-up-industry" not in INDUSTRY_PROFILES

    with pytest.raises(ValidationError):
        AnalysisConfig(industry="totally-made-up-industry")
