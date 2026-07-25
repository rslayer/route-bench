"""Reachability diagnosis: flag routes with an unreachable planned leg."""

from __future__ import annotations

import math
from datetime import datetime

from routebench.analysis.diagnosis.reachability import ReachabilityAnalysis, _planned_legs
from routebench.core.schemas import Fleet, Route, Stop
from routebench.infra.matrix.base import MatrixResult

INF = float("inf")
_START = datetime(2025, 6, 10, 8, 0)


def _route(route_id: str, n_stops: int) -> Route:
    stops = [
        Stop(route_id=route_id, stop_sequence=i, latitude=32.0 + i * 0.01, longitude=-96.0)
        for i in range(1, n_stops + 1)
    ]
    return Route(
        route_id=route_id,
        depot_lat=32.0,
        depot_lon=-96.0,
        planned_start_time=_START,
        stops=stops,
    )


def _matrix(n_stops: int, *, inf_at: tuple[int, int] | None = None) -> MatrixResult:
    """A (n_stops+1) square matrix of finite durations, optionally one leg = inf."""
    dim = n_stops + 1
    durs = [[float(10 * (i + j) + 1) for j in range(dim)] for i in range(dim)]
    if inf_at is not None:
        a, b = inf_at
        durs[a][b] = INF
    return MatrixResult(
        durations_seconds=durs,
        distances_meters=[[100.0] * dim for _ in range(dim)],
        provider="test",
        cached=False,
    )


def _fleet(*routes: Route) -> Fleet:
    return Fleet(routes=list(routes), upload_id="u1", uploaded_at=_START)


class TestPlannedLegs:
    def test_includes_depot_out_and_back(self) -> None:
        # 3 stops: depot->1, 1->2, 2->3, 3->depot
        assert _planned_legs(3) == [(0, 1), (1, 2), (2, 3), (3, 0)]

    def test_empty_for_no_stops(self) -> None:
        assert _planned_legs(0) == []


class TestReachability:
    def test_all_reachable_no_finding(self) -> None:
        fleet = _fleet(_route("R1", 3))
        findings = ReachabilityAnalysis().run(fleet, matrices={"R1": _matrix(3)})
        assert findings == []

    def test_unreachable_leg_flags_the_route(self) -> None:
        fleet = _fleet(_route("R1", 3))
        # 2->3 (matrix indices (2,3)) is unreachable.
        findings = ReachabilityAnalysis().run(fleet, matrices={"R1": _matrix(3, inf_at=(2, 3))})
        assert len(findings) == 1
        f = findings[0]
        assert f.category == "reachability"
        assert f.severity == "high"
        assert f.references.route_ids == ["R1"]
        assert f.evidence[0].metric_name == "unreachable_legs"
        assert f.evidence[0].actual_value == 1.0

    def test_unreachable_return_to_depot_is_caught(self) -> None:
        fleet = _fleet(_route("R1", 2))
        # last->depot is (n_stops, 0) = (2, 0)
        findings = ReachabilityAnalysis().run(fleet, matrices={"R1": _matrix(2, inf_at=(2, 0))})
        assert len(findings) == 1

    def test_only_affected_routes_are_flagged(self) -> None:
        fleet = _fleet(_route("R1", 3), _route("R2", 3))
        findings = ReachabilityAnalysis().run(
            fleet,
            matrices={"R1": _matrix(3, inf_at=(0, 1)), "R2": _matrix(3)},
        )
        assert [f.references.route_ids[0] for f in findings] == ["R1"]

    def test_missing_matrix_is_skipped_not_errored(self) -> None:
        fleet = _fleet(_route("R1", 3))
        assert ReachabilityAnalysis().run(fleet, matrices={}) == []

    def test_finding_hypothesis_is_actionable(self) -> None:
        fleet = _fleet(_route("R1", 2))
        findings = ReachabilityAnalysis().run(fleet, matrices={"R1": _matrix(2, inf_at=(0, 1))})
        assert "coordinate" in findings[0].suggested_investigation.lower()


class TestApplicability:
    def test_applicable_with_stops(self) -> None:
        assert ReachabilityAnalysis().applicability_check(_fleet(_route("R1", 1))).is_applicable

    def test_not_applicable_without_stops(self) -> None:
        assert not ReachabilityAnalysis().applicability_check(_fleet()).is_applicable


def test_registered_in_the_tool_registry() -> None:
    import routebench.analysis.diagnosis  # noqa: F401  (registers on import)
    from routebench.analysis.tools import TOOLS

    assert "analyze_reachability" in TOOLS


def test_isfinite_sanity() -> None:
    assert not math.isfinite(INF)
