"""Industry benchmark profiles and configurable grading weights."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from routebench.core.config import AnalysisConfig
from routebench.core.industry import (
    DEFAULT_WEIGHTS,
    INDUSTRY_PROFILES,
    GradingWeights,
    IndustryProfile,
    get_profile,
)


class TestGradingWeights:
    def test_must_sum_to_one(self) -> None:
        with pytest.raises(ValidationError):
            GradingWeights(sequencing=0.5, fleet=0.5, time=0.5, compliance=0.0, density=0.0)

    def test_valid_weights_accepted(self) -> None:
        w = GradingWeights(sequencing=0.2, fleet=0.2, time=0.2, compliance=0.2, density=0.2)
        assert w.as_dict() == {
            "sequencing": 0.2,
            "fleet": 0.2,
            "time": 0.2,
            "compliance": 0.2,
            "density": 0.2,
        }

    def test_default_matches_historical_blend(self) -> None:
        assert DEFAULT_WEIGHTS.as_dict() == {
            "sequencing": 0.25,
            "fleet": 0.20,
            "time": 0.20,
            "compliance": 0.20,
            "density": 0.15,
        }


class TestProfiles:
    def test_expected_verticals_present(self) -> None:
        assert set(INDUSTRY_PROFILES) == {
            "courier",
            "big_bulky",
            "dsd_quickdrop",
            "dsd_merchandising",
        }

    @pytest.mark.parametrize("key", list(INDUSTRY_PROFILES))
    def test_every_profile_weights_sum_to_one(self, key: str) -> None:
        # Construction already validates, but pin it so a future edit can't drift.
        total = sum(INDUSTRY_PROFILES[key].grading_weights.as_dict().values())
        assert total == pytest.approx(1.0)

    @pytest.mark.parametrize("key", list(INDUSTRY_PROFILES))
    def test_triples_close_against_the_shift(self, key: str) -> None:
        """The headline stop count at the default service time must fit the shift
        with room to drive — the whole point of the coherent-triple design."""
        p = INDUSTRY_PROFILES[key]
        low_stops = p.stops_per_route[0]
        service_budget_min = p.shift_hours * 60.0 * 0.75  # leave >=25% for driving
        assert low_stops * p.default_service_minutes <= service_budget_min, (
            f"{key}: {low_stops} stops x {p.default_service_minutes} min exceeds the "
            f"service budget of a {p.shift_hours}h shift"
        )

    def test_get_profile_none_is_agnostic(self) -> None:
        assert get_profile(None) is None

    def test_get_profile_unknown_is_none(self) -> None:
        assert get_profile("spaceflight") is None

    def test_get_profile_known(self) -> None:
        p = get_profile("courier")
        assert isinstance(p, IndustryProfile)
        assert p.default_service_minutes == 2.0

    def test_inverted_band_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IndustryProfile(
                key="x",
                label="x",
                description="x",
                default_service_minutes=10,
                shift_hours=9,
                stops_per_route=(30, 10),  # inverted
                service_minutes_band=(1, 5),
                grading_weights=DEFAULT_WEIGHTS,
            )


class TestConfigIntegration:
    def test_config_accepts_industry_and_weights(self) -> None:
        cfg = AnalysisConfig(
            industry="courier",
            grading_weights=INDUSTRY_PROFILES["courier"].grading_weights,
        )
        assert cfg.industry == "courier"
        assert cfg.grading_weights is not None
        assert cfg.grading_weights.sequencing == 0.35

    def test_config_defaults_are_agnostic(self) -> None:
        cfg = AnalysisConfig()
        assert cfg.industry is None
        assert cfg.grading_weights is None


def test_unknown_industry_key_is_rejected() -> None:
    """A typo'd/fabricated industry must 422 at config construction, like an
    unknown traffic profile — not silently resolve to None (robustness run 4)."""
    with pytest.raises(ValidationError):
        AnalysisConfig(industry="totally-made-up-industry")
