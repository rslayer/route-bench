"""Volume-based service-time model for F&B / DSD.

Service time at a delivery scales with how much is dropped: a stop leaving 3
cases is quick; one leaving 40 with a full cooler rotation is not. Modelled as a
line — ``service = base + per_unit * demand``.

Best practice is to *learn* it from the ground truth, not assume it. Most uploads
carry real service times on many stops, so those ``(demand, service)`` pairs are
fitted by least squares, and the fitted line fills in the stops that are missing
a time. The industry profile only *seeds* the model — used when there are too few
observed stops, no spread in demand, or a nonsensical (negative-slope) fit to
learn from.

This is within-upload learning: it learns from the operator's own data in this
file. Learning *across* uploads over time (a persistent, refined per-region model)
is the natural next step, but it means retaining service-time observations — a
data-retention decision, deferred.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Below this many observed stops, a two-parameter line is not worth trusting;
# fall back to the profile seed.
_MIN_OBSERVATIONS = 5


@dataclass(frozen=True)
class VolumeServiceModel:
    """A fitted (or seeded) linear service-time model."""

    base_minutes: float
    per_unit_minutes: float
    source: str  # "fitted" (learned from the upload) or "seed" (profile default)
    n_observations: int

    def minutes_for(self, demand: float | None) -> float:
        """Estimated service time for a stop delivering `demand` units."""
        d = max(0.0, demand) if demand is not None else 0.0
        return max(0.0, self.base_minutes + self.per_unit_minutes * d)


def fit_or_seed(
    observations: list[tuple[float | None, float | None]],
    *,
    seed_base: float,
    seed_per_unit: float,
) -> VolumeServiceModel:
    """Fit ``service = base + per_unit * demand`` from observed (demand, service)
    pairs, or fall back to the seed.

    The seed wins when there are fewer than `_MIN_OBSERVATIONS` usable points, no
    spread in demand (a flat line through one x is meaningless), or the fit is
    nonsensical for a service model — a negative slope (more volume, less time) or
    a negative intercept (a zero-demand stop taking negative time).
    """
    usable = [
        (float(d), float(s))
        for d, s in observations
        if d is not None
        and s is not None
        and np.isfinite(d)
        and np.isfinite(s)
        and d >= 0
        and s >= 0
    ]
    n = len(usable)
    if n >= _MIN_OBSERVATIONS:
        demand = np.array([d for d, _ in usable], dtype=float)
        service = np.array([s for _, s in usable], dtype=float)
        if float(demand.std()) > 1e-9:
            slope, intercept = (float(c) for c in np.polyfit(demand, service, 1))
            if np.isfinite(slope) and np.isfinite(intercept) and slope >= 0.0 and intercept >= 0.0:
                return VolumeServiceModel(intercept, slope, "fitted", n)
    return VolumeServiceModel(seed_base, seed_per_unit, "seed", n)
