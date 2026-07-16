"""Phase 10.6 Part C task 10: the rubric cannot drift silently.

The curated sample fleet's grade is pinned here. Any change to a breakpoint, a
weight, or a dimension formula moves these numbers and fails CI — which is the
point. A rubric change is a deliberate act: bump GRADING_VERSION, then update
this snapshot in the same commit, so an old report is never quietly reinterpreted
under a new rubric.

The fleet is deterministic (hand-placed coordinates, no RNG) and the matrix is
stubbed, so these numbers are stable without OSRM.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from routebench.analysis.scoring import compute_scorecard
from routebench.analysis.scoring.grading import GRADING_VERSION, compute_grade
from routebench.core.config import AnalysisConfig
from routebench.core.validation import validate_csv
from tests.analysis.test_traffic import FlatProvider

SAMPLE_CSV = Path(__file__).parent.parent.parent / "data" / "samples" / "v1" / "sample_fleet.csv"

# Pinned expectations. Update ONLY alongside a GRADING_VERSION bump.
EXPECTED_VERSION = "1.0"
EXPECTED_DIMENSIONS = ["sequencing", "fleet", "time", "compliance", "density"]


@pytest.fixture(scope="module")
def sample_grade():
    """Grade the curated sample fleet, without OSRM."""
    if not SAMPLE_CSV.exists():
        # The generator is committed; regenerate rather than skip, so this test
        # cannot rot into a silent pass.
        subprocess.run(
            [sys.executable, "scripts/generate_sample_fleet.py"],
            cwd=SAMPLE_CSV.parent.parent.parent.parent,
            check=True,
            capture_output=True,
        )
    fleet, report = validate_csv(SAMPLE_CSV)
    assert fleet is not None, f"sample fleet failed validation: {report.errors}"

    fleet_metrics, route_metrics = compute_scorecard(fleet, FlatProvider(), AnalysisConfig())
    return compute_grade(fleet_metrics, route_metrics, findings=[], benchmark=None)


class TestSampleFleetGrade:
    def test_version_is_pinned(self, sample_grade) -> None:
        """A version bump must be deliberate and land with a snapshot update."""
        assert sample_grade.grading_version == EXPECTED_VERSION == GRADING_VERSION

    def test_all_five_dimensions_present_in_order(self, sample_grade) -> None:
        assert [d.key for d in sample_grade.dimensions] == EXPECTED_DIMENSIONS

    def test_overall_score_is_stable(self, sample_grade) -> None:
        """The number that moves when anyone touches the rubric.

        Pinned loosely (0.5) rather than exactly: the sample fleet is
        deterministic, but a tolerance keeps this from failing on floating-point
        noise while still catching any real rubric change, which moves scores by
        whole points.
        """
        assert sample_grade.overall.score == pytest.approx(SNAPSHOT["overall_score"], abs=0.5)

    def test_overall_letter_is_stable(self, sample_grade) -> None:
        assert sample_grade.overall.letter == SNAPSHOT["overall_letter"]

    @pytest.mark.parametrize("key", EXPECTED_DIMENSIONS)
    def test_dimension_scores_are_stable(self, sample_grade, key: str) -> None:
        dimension = next(d for d in sample_grade.dimensions if d.key == key)
        expected = SNAPSHOT["dimensions"][key]
        assert dimension.not_graded == expected["not_graded"], f"{key} grading changed"
        if expected["not_graded"]:
            assert dimension.score is None
        else:
            assert dimension.score == pytest.approx(expected["score"], abs=0.5)
            assert dimension.letter == expected["letter"]

    @pytest.mark.parametrize("key", EXPECTED_DIMENSIONS)
    def test_dimension_bases_are_stable(self, sample_grade, key: str) -> None:
        """A basis change means the fleet degraded differently — worth failing on."""
        dimension = next(d for d in sample_grade.dimensions if d.key == key)
        assert dimension.basis == SNAPSHOT["dimensions"][key]["basis"]


class TestSnapshotIsHonest:
    """Guards against the snapshot passing vacuously."""

    def test_the_sample_fleet_actually_grades(self, sample_grade) -> None:
        assert sample_grade.overall.score is not None
        graded = [d for d in sample_grade.dimensions if not d.not_graded]
        assert len(graded) >= 3, "a snapshot over mostly-ungraded dimensions proves little"

    def test_inputs_are_recorded_for_graded_dimensions(self, sample_grade) -> None:
        for dimension in sample_grade.dimensions:
            if not dimension.not_graded:
                assert dimension.inputs, f"{dimension.key} has no inputs to explain its score"


# ---------------------------------------------------------------------------
# THE SNAPSHOT. Update only with a GRADING_VERSION bump, in the same commit.
# Regenerate with: uv run python -m tests.analysis.test_grade_snapshot
#
# These come from a STUB matrix (FlatProvider), not real OSRM, so they are not
# the grade the live sample report will show — a uniform matrix makes every
# route's sequencing_index exactly 1.0, which is why sequencing scores 95 here
# despite R001 being a deliberate zigzag. That does not weaken the guard: the
# job of this snapshot is to notice a RUBRIC change, and any such change moves
# these numbers regardless of where the metrics came from. Keeping the matrix
# stubbed is what makes it runnable in CI with no OSRM.
#
# D+ is the right neighbourhood: the sample fleet was hand-built to exhibit a
# defect in every diagnosis category, so a mediocre grade means the rubric is
# reading it correctly.
# ---------------------------------------------------------------------------

SNAPSHOT: dict = {
    "overall_score": 67.54,
    "overall_letter": "D+",
    "dimensions": {
        "sequencing": {"score": 95.00, "letter": "A", "basis": "heuristic", "not_graded": False},
        "fleet": {"score": 29.23, "letter": "F", "basis": "balance_only", "not_graded": False},
        "time": {"score": 74.23, "letter": "C", "basis": "absolute", "not_graded": False},
        "compliance": {"score": 40.50, "letter": "F", "basis": "absolute", "not_graded": False},
        "density": {
            "score": 100.00,
            "letter": "A+",
            "basis": "fleet_relative",
            "not_graded": False,
        },
    },
}


def _print_snapshot() -> None:
    """Emit a fresh SNAPSHOT literal to paste above."""
    fleet, _ = validate_csv(SAMPLE_CSV)
    assert fleet is not None
    fleet_metrics, route_metrics = compute_scorecard(fleet, FlatProvider(), AnalysisConfig())
    grade = compute_grade(fleet_metrics, route_metrics, findings=[], benchmark=None)

    print("SNAPSHOT: dict = {")
    print(f'    "overall_score": {grade.overall.score:.2f},')
    print(f'    "overall_letter": "{grade.overall.letter}",')
    print('    "dimensions": {')
    for d in grade.dimensions:
        score = "None" if d.score is None else f"{d.score:.2f}"
        letter = "None" if d.letter is None else f'"{d.letter}"'
        print(
            f'        "{d.key}": {{"score": {score}, "letter": {letter}, '
            f'"basis": "{d.basis}", "not_graded": {d.not_graded}}},'
        )
    print("    },")
    print("}")


if __name__ == "__main__":
    _print_snapshot()
