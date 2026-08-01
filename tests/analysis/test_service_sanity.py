"""Service-time sanity: flag stops whose dwell time is implausible for the vertical."""

from __future__ import annotations

from datetime import datetime

from routebench.analysis.diagnosis.service_sanity import ServiceSanityAnalysis
from routebench.core.industry import INDUSTRY_PROFILES
from routebench.core.schemas import Fleet, Route, Stop

_START = datetime(2025, 6, 10, 8, 0)
COURIER = INDUSTRY_PROFILES["courier"]  # service band (0.5, 8.0), default 2.0


def _route(route_id: str, service_times: list[float]) -> Route:
    stops = [
        Stop(
            route_id=route_id,
            stop_sequence=i,
            latitude=32.0 + i * 0.01,
            longitude=-96.0,
            service_time_minutes=st,
        )
        for i, st in enumerate(service_times, start=1)
    ]
    return Route(
        route_id=route_id, depot_lat=32.0, depot_lon=-96.0, planned_start_time=_START, stops=stops
    )


def _fleet(*routes: Route) -> Fleet:
    return Fleet(routes=list(routes), upload_id="u1", uploaded_at=_START)


class TestServiceSanity:
    def test_no_profile_no_findings(self) -> None:
        fleet = _fleet(_route("R1", [45.0, 50.0]))  # wildly out of any band
        assert ServiceSanityAnalysis().run(fleet) == []  # no industry_profile kwarg

    def test_in_band_no_findings(self) -> None:
        fleet = _fleet(_route("R1", [2.0, 3.0, 1.5]))  # all within courier 0.5-8
        assert ServiceSanityAnalysis().run(fleet, industry_profile=COURIER) == []

    def test_above_band_flags_route(self) -> None:
        fleet = _fleet(_route("R1", [2.0, 45.0, 3.0]))  # 45 min courier stop = suspicious
        findings = ServiceSanityAnalysis().run(fleet, industry_profile=COURIER)
        assert len(findings) == 1
        f = findings[0]
        assert f.category == "data_quality"
        assert f.severity == "medium"
        assert f.references.route_ids == ["R1"]
        assert f.references.stop_sequences == [("R1", 2)]  # the 45-min stop is seq 2
        assert f.evidence[0].actual_value == 1.0  # one out-of-band stop

    def test_below_band_flags_route(self) -> None:
        # A big-and-bulky install tagged 5 min (band 30-240) is implausibly short.
        bulky = INDUSTRY_PROFILES["big_bulky"]
        fleet = _fleet(_route("R1", [90.0, 5.0]))
        findings = ServiceSanityAnalysis().run(fleet, industry_profile=bulky)
        assert len(findings) == 1
        assert findings[0].references.stop_sequences == [("R1", 2)]

    def test_only_affected_routes_flagged(self) -> None:
        fleet = _fleet(_route("R1", [2.0, 3.0]), _route("R2", [2.0, 99.0]))
        findings = ServiceSanityAnalysis().run(fleet, industry_profile=COURIER)
        assert [f.references.route_ids[0] for f in findings] == ["R2"]

    def test_worst_offender_reported(self) -> None:
        fleet = _fleet(_route("R1", [2.0, 20.0, 60.0]))  # two over band; 60 is worst
        findings = ServiceSanityAnalysis().run(fleet, industry_profile=COURIER)
        worst = next(e for e in findings[0].evidence if e.metric_name == "worst_service_minutes")
        assert worst.actual_value == 60.0
        assert findings[0].evidence[0].actual_value == 2.0  # both 20 and 60 out of band


def test_registered_in_the_tool_registry() -> None:
    import routebench.analysis.diagnosis  # noqa: F401
    from routebench.analysis.tools import TOOLS

    assert "analyze_service_sanity" in TOOLS
