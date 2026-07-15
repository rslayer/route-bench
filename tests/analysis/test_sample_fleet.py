"""The curated sample fleet must actually exercise every diagnosis.

A sample report is only a demonstration if each analysis has something to say.
These tests pin the fleet's design: if someone moves a stop and a category stops
firing, the sample quietly degrades into a random fleet, and this catches it.

Distances here come from haversine, not OSRM, so the numbers are not the ones
the real report shows. What is being checked is the geometry of the design —
a zigzag is a zigzag whichever provider measures it.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import datetime
from pathlib import Path

import pytest

from routebench.analysis.benchmark import FleetBenchmarkTool, RouteBenchmarkTool
from routebench.analysis.benchmark.fleet_matrix import get_fleet_matrix
from routebench.analysis.diagnosis.compliance import ComplianceAnalysis
from routebench.analysis.diagnosis.dispatch import DispatchAnalysis
from routebench.analysis.diagnosis.outliers import OutlierAnalysis
from routebench.analysis.diagnosis.sequencing import SequencingAnalysis
from routebench.analysis.diagnosis.territory import TerritoryAnalysis
from routebench.analysis.diagnosis.time_pressure import TimePressureAnalysis
from routebench.analysis.scoring.distance import get_route_matrix
from routebench.core.config import URBAN_US_PROFILE, WorkRules
from routebench.core.findings import Finding
from routebench.core.schemas import Fleet
from routebench.core.validation import validate_csv
from routebench.infra.matrix.base import MatrixResult
from routebench.infra.matrix.traffic import TrafficAdjustedProvider

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_sample_fleet.py"

METERS_PER_MILE = 1609.34
ASSUMED_MPH = 30.0


def _load_generator():  # type: ignore[no-untyped-def]
    """Import the generator by path; scripts/ is not an installed package."""
    spec = importlib.util.spec_from_file_location("generate_sample_fleet", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_sample_fleet"] = module
    spec.loader.exec_module(module)
    return module


class HaversineProvider:
    """Straight-line stand-in for OSRM, so the design is testable offline."""

    name: str = "haversine"

    def get_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        departure_time: datetime | None = None,
        origin_departure_times: list[datetime] | None = None,
    ) -> MatrixResult:
        distances: list[list[float]] = []
        durations: list[list[float]] = []
        for olat, olon in origins:
            drow: list[float] = []
            trow: list[float] = []
            for dlat, dlon in destinations:
                miles = _haversine_miles(olat, olon, dlat, dlon)
                drow.append(miles * METERS_PER_MILE)
                trow.append(miles / ASSUMED_MPH * 3600.0)
            distances.append(drow)
            durations.append(trow)
        return MatrixResult(
            durations_seconds=durations,
            distances_meters=distances,
            provider="haversine",
            cached=False,
        )


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat, dlon = lat2_r - lat1_r, lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


@pytest.fixture(scope="module")
def sample_fleet(tmp_path_factory: pytest.TempPathFactory) -> Fleet:
    """Generate the sample CSV and parse it back through real validation."""
    out = tmp_path_factory.mktemp("sample") / "fleet.csv"
    _load_generator().write_fleet(str(out))
    fleet, report = validate_csv(out)
    assert report.is_valid, f"sample fleet must validate: {report.errors}"
    assert fleet is not None
    return fleet


@pytest.fixture(scope="module")
def matrices(sample_fleet: Fleet) -> dict[str, MatrixResult]:
    provider = HaversineProvider()
    return {
        r.route_id: get_route_matrix(r, provider, WorkRules())  # type: ignore[arg-type]
        for r in sample_fleet.routes
    }


def _categories(findings: list[Finding]) -> set[str]:
    return {f.category for f in findings}


def _routes(findings: list[Finding]) -> set[str]:
    return {rid for f in findings for rid in f.references.route_ids}


class TestSampleFleetShape:
    """The input itself."""

    def test_validates_cleanly(self, sample_fleet: Fleet) -> None:
        assert len(sample_fleet.routes) == 6
        assert sample_fleet.total_stops() == 36

    def test_all_routes_share_one_depot(self, sample_fleet: Fleet) -> None:
        """Required, or the fleet-level benchmark skips itself."""
        from routebench.analysis.benchmark.fleet_matrix import fleet_depot

        assert fleet_depot(sample_fleet) is not None

    def test_generator_is_deterministic(self, tmp_path: Path) -> None:
        """The sample report is only reproducible if its input is."""
        gen = _load_generator()
        a, b = tmp_path / "a.csv", tmp_path / "b.csv"
        gen.write_fleet(str(a))
        gen.write_fleet(str(b))
        assert a.read_text() == b.read_text()


class TestEveryDiagnosisFires:
    """One route was placed for each of these; none may go quiet."""

    def test_sequencing_flags_the_zigzag_route(
        self, sample_fleet: Fleet, matrices: dict[str, MatrixResult]
    ) -> None:
        findings = SequencingAnalysis().run(sample_fleet, matrices=matrices)
        assert "sequencing" in _categories(findings)
        assert "R001" in _routes(findings), "R001 is the designed sequencing offender"

    def test_time_pressure_flags_the_idle_route(
        self, sample_fleet: Fleet, matrices: dict[str, MatrixResult]
    ) -> None:
        findings = TimePressureAnalysis().run(
            sample_fleet, matrices=matrices, work_rules=WorkRules()
        )
        assert "time_pressure" in _categories(findings)
        assert "R002" in _routes(findings), "R002 waits hours for its 11:00 windows"

    def test_outliers_flags_the_stranded_stop(
        self, sample_fleet: Fleet, matrices: dict[str, MatrixResult]
    ) -> None:
        findings = OutlierAnalysis().run(sample_fleet, matrices=matrices)
        assert "outlier" in _categories(findings)
        assert "R003" in _routes(findings), "R003 carries the Mesquite outlier"

    def test_territory_flags_the_overlapping_pair(self, sample_fleet: Fleet) -> None:
        findings = TerritoryAnalysis().run(sample_fleet)
        assert "territory" in _categories(findings)

    def test_dispatch_flags_the_start_time_cluster(self, sample_fleet: Fleet) -> None:
        findings = DispatchAnalysis().run(sample_fleet)
        assert "dispatch" in _categories(findings), "5 of 6 routes leave within 15 minutes"

    def test_compliance_fires_under_a_peak_profile(self, sample_fleet: Fleet) -> None:
        """R006's afternoon windows have no slack for the 16:00-18:30 band."""
        provider = TrafficAdjustedProvider(HaversineProvider(), URBAN_US_PROFILE)
        banded = {
            r.route_id: get_route_matrix(r, provider, WorkRules()) for r in sample_fleet.routes
        }
        findings = ComplianceAnalysis().run(
            sample_fleet,
            matrices=banded,
            work_rules=WorkRules(),
            traffic_profile=URBAN_US_PROFILE,
        )
        assert "compliance" in _categories(findings)

    def test_route_benchmark_finds_a_real_gap(
        self, sample_fleet: Fleet, matrices: dict[str, MatrixResult]
    ) -> None:
        sink: dict[str, object] = {}
        RouteBenchmarkTool().run(
            sample_fleet,
            matrices=matrices,
            work_rules=WorkRules(),
            time_limit_s=2,
            benchmark_sink=sink,
        )
        per_route = sink["per_route"]
        assert isinstance(per_route, dict)
        assert per_route["R001"].distance_gap_pct > 5.0, "the zigzag must be beatable"

    def test_fleet_benchmark_proposes_migrations(self, sample_fleet: Fleet) -> None:
        provider = HaversineProvider()
        combined = get_fleet_matrix(sample_fleet, provider, WorkRules())  # type: ignore[arg-type]
        sink: dict[str, object] = {}
        FleetBenchmarkTool().run(
            sample_fleet,
            matrices={
                r.route_id: get_route_matrix(r, provider, WorkRules())  # type: ignore[arg-type]
                for r in sample_fleet.routes
            },
            combined_matrix=combined,
            work_rules=WorkRules(),
            time_limit_s=3,
            benchmark_sink=sink,
        )
        fleet_level = sink.get("fleet_level")
        assert fleet_level is not None
        assert fleet_level.actual_total_distance > 0
