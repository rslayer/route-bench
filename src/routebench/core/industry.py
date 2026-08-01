"""Industry benchmark profiles.

Different last-mile operations stress completely different parts of the day, so
the same route can be "good" in one industry and "bad" in another. A profile is
a coherent preset that reshapes the analysis for a vertical:

  * default per-stop service time (fills stops the CSV leaves blank),
  * the shift length the plan is judged against,
  * an expected stops-per-route band and a plausible service-time band, used to
    flag data that does not fit the vertical (a courier stop tagged 45 min, a
    bulky route with 60 stops),
  * the grading weights — what "good" means here.

The numbers are coherent *triples*: stops-per-route, service time, and shift are
bound by `stops x (service + drive) <= shift`, so each preset is built to close
rather than pairing a headline stop count with a service time that could never
fit the day. See docs and the validated industry research.

This is the single source of truth; the API exposes it and the web panel applies
it, so a chosen profile still shows the user exactly what will run.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# The five grading dimensions. Kept in sync with analysis/scoring/grading.py by
# GradingWeights below — its field names are the dimension keys.
_DIMENSIONS = ("sequencing", "fleet", "time", "compliance", "density")


class GradingWeights(BaseModel):
    """Weights for the composite grade, one per dimension, summing to 1.0.

    Dimension scores stay universal and comparable across industries; only this
    blend changes, so a courier's grade emphasises sequencing and density while a
    big-and-bulky grade emphasises window compliance.
    """

    model_config = {"extra": "forbid"}

    sequencing: float = Field(ge=0, le=1)
    fleet: float = Field(ge=0, le=1)
    time: float = Field(ge=0, le=1)
    compliance: float = Field(ge=0, le=1)
    density: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> GradingWeights:
        total = self.sequencing + self.fleet + self.time + self.compliance + self.density
        if abs(total - 1.0) > 1e-6:
            msg = f"grading weights must sum to 1.0, got {total:.4f}"
            raise ValueError(msg)
        return self

    def as_dict(self) -> dict[str, float]:
        return {d: getattr(self, d) for d in _DIMENSIONS}


# The default (industry-agnostic) blend, matching the historical grade so an
# untouched run is unchanged.
DEFAULT_WEIGHTS = GradingWeights(
    sequencing=0.25, fleet=0.20, time=0.20, compliance=0.20, density=0.15
)


class IndustryProfile(BaseModel):
    """A named preset for one vertical."""

    model_config = {"extra": "forbid"}

    key: str
    label: str
    description: str
    # Fills Stop.service_time_minutes when the CSV leaves it blank. A real
    # per-stop value in the upload always wins over this.
    default_service_minutes: float = Field(gt=0)
    shift_hours: float = Field(gt=0, le=24)
    # Expected stops per route — a sanity band, not a hard limit.
    stops_per_route: tuple[int, int]
    # Plausible per-stop service time (minutes); values outside are flagged as a
    # likely data-quality problem rather than silently graded.
    service_minutes_band: tuple[float, float]
    grading_weights: GradingWeights

    @model_validator(mode="after")
    def _bands_ordered(self) -> IndustryProfile:
        if self.stops_per_route[0] > self.stops_per_route[1]:
            raise ValueError("stops_per_route band is inverted")
        if self.service_minutes_band[0] > self.service_minutes_band[1]:
            raise ValueError("service_minutes_band is inverted")
        return self


# The presets. Triples are built to close against the shift (see module docstring
# and the validated research): F&B is split into two coherent profiles because a
# single "20-30 stops at 30-45 min" pairing cannot fit a day.
INDUSTRY_PROFILES: dict[str, IndustryProfile] = {
    "courier": IndustryProfile(
        key="courier",
        label="Courier / parcel",
        description=(
            "Dense last-mile parcel delivery — ~150-200 quick drops a day. Sequencing "
            "efficiency and stop density decide the outcome."
        ),
        default_service_minutes=2.0,
        shift_hours=9.0,
        stops_per_route=(120, 220),
        service_minutes_band=(0.5, 8.0),
        grading_weights=GradingWeights(
            sequencing=0.35, fleet=0.10, time=0.20, compliance=0.10, density=0.25
        ),
    ),
    "big_bulky": IndustryProfile(
        key="big_bulky",
        label="Big & bulky / white-glove",
        description=(
            "Furniture and appliance delivery with install and haul-away — ~5-10 stops, "
            "long service, tight appointment windows. Window compliance dominates."
        ),
        default_service_minutes=90.0,
        shift_hours=9.0,
        stops_per_route=(4, 12),
        service_minutes_band=(30.0, 240.0),
        grading_weights=GradingWeights(
            sequencing=0.10, fleet=0.20, time=0.25, compliance=0.35, density=0.10
        ),
    ),
    "dsd_quickdrop": IndustryProfile(
        key="dsd_quickdrop",
        label="Food & beverage — DSD quick-drop",
        description=(
            "Direct-store-delivery to c-stores, bars and small grocery — ~20-25 fast "
            "stops. Territory balance and delivery-window compliance matter most."
        ),
        default_service_minutes=18.0,
        shift_hours=10.0,
        stops_per_route=(15, 35),
        service_minutes_band=(5.0, 30.0),
        grading_weights=GradingWeights(
            sequencing=0.20, fleet=0.30, time=0.15, compliance=0.20, density=0.15
        ),
    ),
    "dsd_merchandising": IndustryProfile(
        key="dsd_merchandising",
        label="Food & beverage — large-format merchandising",
        description=(
            "DSD to big grocery/retail with full stock rotation and displays — ~10-15 "
            "stops at 30-45 min each. Scheduled windows and territory balance dominate."
        ),
        default_service_minutes=40.0,
        shift_hours=10.0,
        stops_per_route=(8, 16),
        service_minutes_band=(25.0, 60.0),
        grading_weights=GradingWeights(
            sequencing=0.15, fleet=0.30, time=0.15, compliance=0.30, density=0.10
        ),
    ),
}


def get_profile(key: str | None) -> IndustryProfile | None:
    """Look up a profile by key, or None for the industry-agnostic default."""
    if key is None:
        return None
    return INDUSTRY_PROFILES.get(key)
