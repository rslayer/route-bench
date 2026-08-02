"""The per-request `config` field must not be a CPU/DoS lever.

`POST /sessions` parses the `config` form field into `AnalysisConfig`, so any
unbounded numeric knob that drives solver time lets a caller make each job burn
to the job timeout with a tiny input and exhaust the single-worker queue. These
tests pin upper bounds on every solver-time knob.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from routebench.core.config import AnalysisConfig

# (field, an over-cap value that must be rejected)
OVER_CAP = [
    ("route_benchmark_time_limit_s", 601),
    ("fleet_benchmark_time_limit_s", 601),
    ("route_min_time_limit_s", 601),
    ("fleet_min_time_limit_s", 601),
    ("solver_seconds_per_route_stop", 61),
    ("solver_seconds_per_fleet_stop", 61),
    ("route_solver_envelope_s", 601),
    ("route_min_floor_s", 601),
    ("route_benchmark_workers", 65),
]


@pytest.mark.parametrize("field,value", OVER_CAP)
def test_over_cap_solver_knob_is_rejected(field, value):
    with pytest.raises(ValidationError):
        AnalysisConfig(**{field: value})


@pytest.mark.parametrize("field", [f for f, _ in OVER_CAP])
def test_at_cap_boundary_is_accepted(field):
    # the documented ceiling itself must remain a legal operator value
    cap = AnalysisConfig.model_fields[field].metadata
    le = next(getattr(m, "le", None) for m in cap if getattr(m, "le", None) is not None)
    cfg = AnalysisConfig(**{field: le})
    assert getattr(cfg, field) == le


@pytest.mark.parametrize(
    "field", ["solver_seconds_per_route_stop", "solver_seconds_per_fleet_stop"]
)
def test_inf_and_nan_solver_coefficients_are_rejected(field):
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            AnalysisConfig(**{field: bad})


def test_defaults_are_within_caps():
    # a sanity check that the shipped defaults did not drift above their ceilings
    AnalysisConfig()
