"""Phase 10.6: the grading engine.

The grade is the product promise, so the properties that matter are: it is
deterministic, it degrades rather than errors, every reported input is
recomputable from the artifact, and the rubric cannot drift silently.
"""

from __future__ import annotations

import pytest

from routebench.analysis.scoring.grading import (
    GAP_PCT_BREAKPOINTS,
    GRADING_VERSION,
    WEIGHTS,
    _cv,
    compute_grade,
    interpolate,
    letter_for,
)
from routebench.core.findings import (
    BenchmarkResult,
    FleetBenchmark,
    FleetMetrics,
    RouteBenchmark,
    RouteMetrics,
)


def _rm(
    route_id: str,
    *,
    total_time_hours: float = 8.0,
    stops_per_mile: float = 0.3,
    idle_time_hours: float = 0.3,
    violations: int = 0,
    stops_with_windows: int = 10,
    shift_overrun_minutes: float = 0.0,
    lunch_taken: bool = True,
    stop_count: int = 10,
    sequencing_index: float | None = 1.1,
    capacity: dict[str, float] | None = None,
) -> RouteMetrics:
    return RouteMetrics(
        route_id=route_id,
        total_distance_miles=30.0,
        total_time_hours=total_time_hours,
        drive_time_hours=6.0,
        service_time_hours=1.0,
        idle_time_hours=idle_time_hours,
        stop_count=stop_count,
        stops_per_hour=1.2,
        stops_per_mile=stops_per_mile,
        sequencing_index=sequencing_index,
        capacity_utilization=capacity or {},
        time_window_violations=violations,
        stops_with_windows=stops_with_windows,
        shift_overrun_minutes=shift_overrun_minutes,
        lunch_taken_within_window=lunch_taken,
    )


def _fm(n_routes: int) -> FleetMetrics:
    return FleetMetrics(
        total_routes=n_routes,
        total_stops=n_routes * 10,
        total_distance_miles=30.0 * n_routes,
        total_time_hours=8.0 * n_routes,
        routes_over_shift_cap=0,
    )


def _benchmark(
    route_gap: float,
    fleet_gap: float | None = None,
    route_ids: tuple[str, ...] = ("R1", "R2"),
) -> BenchmarkResult:
    return BenchmarkResult(
        per_route={
            rid: RouteBenchmark(
                route_id=rid,
                actual_distance_miles=30.0,
                optimal_distance_miles=27.0,
                distance_gap_pct=route_gap,
                actual_time_hours=8.0,
                optimal_time_hours=7.5,
                time_gap_pct=6.0,
                improvement_gap_pct=route_gap,
                stop_order=[1, 2, 3],
            )
            for rid in route_ids
        },
        fleet_level=(
            None
            if fleet_gap is None
            else FleetBenchmark(
                actual_total_distance=60.0,
                optimal_total_distance=54.0,
                stop_migrations=[],
                improvement_gap_pct=fleet_gap,
            )
        ),
    )


def _two_routes() -> dict[str, RouteMetrics]:
    return {"R1": _rm("R1"), "R2": _rm("R2", total_time_hours=8.4)}


def _dims(grade: object) -> dict[str, object]:
    return {d.key: d for d in grade.dimensions}  # type: ignore[attr-defined]


class TestInterpolation:
    """Piecewise-linear lookup with clamping."""

    def test_exact_breakpoints(self) -> None:
        for x, y in GAP_PCT_BREAKPOINTS:
            assert interpolate(x, GAP_PCT_BREAKPOINTS) == pytest.approx(y)

    def test_midpoint_is_linear(self) -> None:
        """5% sits halfway between (3, 92) and (7, 82)."""
        assert interpolate(5.0, GAP_PCT_BREAKPOINTS) == pytest.approx(87.0)

    def test_clamps_below(self) -> None:
        """A negative gap means the solver found nothing better — a perfect result."""
        assert interpolate(-10.0, GAP_PCT_BREAKPOINTS) == 100.0

    def test_clamps_above(self) -> None:
        """Beyond the table, worse stops being meaningfully worse."""
        assert interpolate(200.0, GAP_PCT_BREAKPOINTS) == 15.0

    def test_never_extrapolates_negative(self) -> None:
        assert interpolate(1e9, GAP_PCT_BREAKPOINTS) > 0

    def test_empty_table(self) -> None:
        assert interpolate(5.0, ()) == 0.0


class TestLetterBands:
    @pytest.mark.parametrize(
        ("score", "letter"),
        [
            (100, "A+"),
            (97, "A+"),
            (96.99, "A"),
            (93, "A"),
            (90, "A-"),
            (87, "B+"),
            (83, "B"),
            (80, "B-"),
            (77, "C+"),
            (73, "C"),
            (70, "C-"),
            (67, "D+"),
            (63, "D"),
            (60, "D-"),
            (59.99, "F"),
            (0, "F"),
        ],
    )
    def test_bands(self, score: float, letter: str) -> None:
        assert letter_for(score) == letter

    def test_boundaries_are_inclusive_floors(self) -> None:
        assert letter_for(93.0) == "A"
        assert letter_for(92.999) == "A-"


class TestSequencing:
    def test_benchmark_basis_when_available(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(7.0))
        seq = _dims(grade)["sequencing"]
        assert seq.basis == "benchmark"
        assert seq.score == pytest.approx(82.0)
        assert seq.inputs["stop_weighted_gap_pct"] == pytest.approx(7.0)

    def test_heuristic_fallback_without_benchmark(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], None)
        seq = _dims(grade)["sequencing"]
        assert seq.basis == "heuristic"
        assert seq.inputs["mean_sequencing_index"] == pytest.approx(1.1)

    def test_gap_is_stop_weighted_not_route_averaged(self) -> None:
        """A 40-stop route must outweigh a 2-stop one."""
        metrics = {"R1": _rm("R1", stop_count=40), "R2": _rm("R2", stop_count=2)}
        benchmark = _benchmark(0.0)
        benchmark.per_route["R1"].improvement_gap_pct = 0.0
        benchmark.per_route["R2"].improvement_gap_pct = 42.0
        grade = compute_grade(_fm(2), metrics, [], benchmark)
        gap = _dims(grade)["sequencing"].inputs["stop_weighted_gap_pct"]
        # Route-averaged would be 21.0; stop-weighted is 2.0.
        assert gap == pytest.approx(2.0)

    def test_negative_gap_scores_perfect(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(-5.0))
        assert _dims(grade)["sequencing"].score == 100.0

    def test_not_graded_without_benchmark_or_index(self) -> None:
        metrics = {"R1": _rm("R1", sequencing_index=None)}
        seq = _dims(compute_grade(_fm(1), metrics, [], None))["sequencing"]
        assert seq.not_graded
        assert seq.score is None


class TestFleetAssignment:
    def test_incremental_gap_avoids_double_counting(self) -> None:
        """Route-level waste is already in sequencing; only the excess is assignment."""
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(7.0, 15.0))
        fleet = _dims(grade)["fleet"]
        assert fleet.inputs["incremental_gap_pct"] == pytest.approx(8.0)
        assert fleet.basis == "benchmark"

    def test_fleet_gap_below_route_gap_floors_at_zero(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(20.0, 5.0))
        assert _dims(grade)["fleet"].inputs["incremental_gap_pct"] == 0.0

    def test_balance_only_when_fleet_benchmark_skipped(self) -> None:
        fleet = _dims(compute_grade(_fm(2), _two_routes(), [], _benchmark(7.0, None)))["fleet"]
        assert fleet.basis == "balance_only"
        assert fleet.score is not None
        assert "time_cv" in fleet.inputs

    def test_balance_only_without_any_benchmark(self) -> None:
        assert (
            _dims(compute_grade(_fm(2), _two_routes(), [], None))["fleet"].basis == "balance_only"
        )

    def test_uneven_times_score_lower(self) -> None:
        even = compute_grade(_fm(2), _two_routes(), [], None)
        uneven = compute_grade(
            _fm(2),
            {"R1": _rm("R1", total_time_hours=3.0), "R2": _rm("R2", total_time_hours=12.0)},
            [],
            None,
        )
        assert _dims(uneven)["fleet"].score < _dims(even)["fleet"].score

    def test_capacity_cv_folds_in_when_present(self) -> None:
        metrics = {
            "R1": _rm("R1", capacity={"units": 0.5}),
            "R2": _rm("R2", total_time_hours=8.4, capacity={"units": 0.9}),
        }
        fleet = _dims(compute_grade(_fm(2), metrics, [], None))["fleet"]
        assert "capacity_cv" in fleet.inputs

    def test_single_route_not_graded(self) -> None:
        fleet = _dims(compute_grade(_fm(1), {"R1": _rm("R1")}, [], None))["fleet"]
        assert fleet.not_graded
        assert fleet.basis == "insufficient_routes"


class TestTimeDiscipline:
    def test_no_idle_no_overrun_is_perfect(self) -> None:
        metrics = {"R1": _rm("R1", idle_time_hours=0.0), "R2": _rm("R2", idle_time_hours=0.0)}
        assert _dims(compute_grade(_fm(2), metrics, [], None))["time"].score == 100.0

    def test_idle_ratio_is_recomputable(self) -> None:
        metrics = {"R1": _rm("R1", idle_time_hours=0.8, total_time_hours=8.0)}
        time = _dims(compute_grade(_fm(1), metrics, [], None))["time"]
        assert time.inputs["idle_ratio"] == pytest.approx(0.1)

    def test_overrun_minutes_penalise_beyond_share(self) -> None:
        """One route 5 minutes over is not one route 3 hours over."""
        small = {"R1": _rm("R1", shift_overrun_minutes=5.0), "R2": _rm("R2")}
        large = {"R1": _rm("R1", shift_overrun_minutes=180.0), "R2": _rm("R2")}
        assert (
            _dims(compute_grade(_fm(2), large, [], None))["time"].score
            < _dims(compute_grade(_fm(2), small, [], None))["time"].score
        )

    def test_overrun_penalty_floors_at_zero(self) -> None:
        metrics = {"R1": _rm("R1", shift_overrun_minutes=100_000.0)}
        assert _dims(compute_grade(_fm(1), metrics, [], None))["time"].score >= 0.0


class TestCompliance:
    def test_violation_rate_is_over_windowed_stops(self) -> None:
        """The denominator is stops that HAVE a window, not all stops."""
        metrics = {"R1": _rm("R1", violations=1, stops_with_windows=10, stop_count=100)}
        compliance = _dims(compute_grade(_fm(1), metrics, [], None))["compliance"]
        assert compliance.inputs["violation_rate_pct"] == pytest.approx(10.0)

    def test_no_windows_grades_operational_only(self) -> None:
        metrics = {"R1": _rm("R1", stops_with_windows=0)}
        compliance = _dims(compute_grade(_fm(1), metrics, [], None))["compliance"]
        assert compliance.basis == "operational_only"
        assert "violation_rate_pct" not in compliance.inputs

    def test_lunch_failure_uses_the_full_range(self) -> None:
        """Half the routes failing must cost half the score, not a quarter.

        The spec's formula reserved 50 points for a depot cutoff that is not
        modelled, so every fleet kept an unloseable half. Lunch alone spans 0-100.
        """
        metrics = {
            "R1": _rm("R1", stops_with_windows=0, lunch_taken=False),
            "R2": _rm("R2", stops_with_windows=0),
        }
        compliance = _dims(compute_grade(_fm(2), metrics, [], None))["compliance"]
        assert compliance.score == pytest.approx(50.0)

    def test_all_lunches_failing_scores_zero(self) -> None:
        metrics = {"R1": _rm("R1", stops_with_windows=0, lunch_taken=False)}
        assert _dims(compute_grade(_fm(1), metrics, [], None))["compliance"].score == 0.0

    def test_perfect_compliance(self) -> None:
        metrics = {"R1": _rm("R1", violations=0)}
        assert _dims(compute_grade(_fm(1), metrics, [], None))["compliance"].score == 100.0


class TestDensity:
    def test_single_route_not_graded(self) -> None:
        density = _dims(compute_grade(_fm(1), {"R1": _rm("R1")}, [], None))["density"]
        assert density.not_graded
        assert density.basis == "insufficient_routes"

    def test_consistent_dispersion_scores_high(self) -> None:
        metrics = {"R1": _rm("R1", stops_per_mile=0.30), "R2": _rm("R2", stops_per_mile=0.31)}
        density = _dims(compute_grade(_fm(2), metrics, [], None))["density"]
        assert density.basis == "fleet_relative"
        assert density.score is not None and density.score > 90

    def test_scattered_dispersion_scores_lower(self) -> None:
        tight = {"R1": _rm("R1", stops_per_mile=0.30), "R2": _rm("R2", stops_per_mile=0.31)}
        wild = {"R1": _rm("R1", stops_per_mile=0.05), "R2": _rm("R2", stops_per_mile=2.0)}
        assert (
            _dims(compute_grade(_fm(2), wild, [], None))["density"].score
            < _dims(compute_grade(_fm(2), tight, [], None))["density"].score
        )

    def test_overlap_findings_penalise(self) -> None:
        from routebench.core.findings import Finding, FindingEvidence, FindingReference

        overlap = Finding(
            category="territory",
            severity="medium",
            confidence=0.75,
            title="Routes R1 and R2: 34% geographic overlap",
            evidence=[
                FindingEvidence(
                    metric_name="geographic_overlap_pct", actual_value=34.0, unit="percent"
                )
            ],
            references=FindingReference(route_ids=["R1", "R2"]),
            hypothesis="h",
            suggested_investigation="i",
        )
        clean = compute_grade(_fm(2), _two_routes(), [], None)
        flagged = compute_grade(_fm(2), _two_routes(), [overlap], None)
        assert _dims(flagged)["density"].inputs["overlapping_route_pairs"] == 1.0
        assert _dims(flagged)["density"].score < _dims(clean)["density"].score

    def test_non_overlap_territory_findings_do_not_count(self) -> None:
        """Depot stress is a territory finding but is not an overlapping pair."""
        from routebench.core.findings import Finding, FindingEvidence, FindingReference

        depot_stress = Finding(
            category="territory",
            severity="medium",
            confidence=0.75,
            title="Depot stress",
            evidence=[
                FindingEvidence(metric_name="depot_distance_miles", actual_value=40.0, unit="miles")
            ],
            references=FindingReference(route_ids=["R1", "R2"]),
            hypothesis="h",
            suggested_investigation="i",
        )
        grade = compute_grade(_fm(2), _two_routes(), [depot_stress], None)
        assert _dims(grade)["density"].inputs["overlapping_route_pairs"] == 0.0


class TestComposite:
    def test_weights_sum_to_one(self) -> None:
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_all_dimensions_graded(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(7.0, 15.0))
        assert all(not d.not_graded for d in grade.dimensions)
        assert grade.overall.score is not None

    def test_renormalizes_over_graded_dimensions(self) -> None:
        """An ungraded dimension must not score zero and drag the composite down."""
        single = compute_grade(_fm(1), {"R1": _rm("R1")}, [], None)
        graded = [d for d in single.dimensions if not d.not_graded]
        expected = sum(WEIGHTS[d.key] * d.score for d in graded) / sum(
            WEIGHTS[d.key] for d in graded
        )
        assert single.overall.score == pytest.approx(expected)

    def test_composite_is_between_its_dimensions(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(7.0, 15.0))
        scores = [d.score for d in grade.dimensions if d.score is not None]
        assert min(scores) <= grade.overall.score <= max(scores)

    def test_overall_letter_matches_score(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(7.0, 15.0))
        assert grade.overall.letter == letter_for(grade.overall.score)

    def test_version_is_recorded(self) -> None:
        assert compute_grade(_fm(2), _two_routes(), [], None).grading_version == GRADING_VERSION


class TestDeterminism:
    """Same inputs, same grade — the whole engine is a pure function."""

    def test_repeated_calls_agree(self) -> None:
        args = (_fm(2), _two_routes(), [], _benchmark(7.0, 15.0))
        first = compute_grade(*args)
        second = compute_grade(*args)
        assert first.model_dump() == second.model_dump()

    def test_dimension_order_is_stable(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], None)
        assert [d.key for d in grade.dimensions] == [
            "sequencing",
            "fleet",
            "time",
            "compliance",
            "density",
        ]


class TestExplainability:
    """Part C: every reported input must be recomputable from the artifact."""

    def test_every_graded_dimension_reports_inputs(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], _benchmark(7.0, 15.0))
        for dimension in grade.dimensions:
            if not dimension.not_graded:
                assert dimension.inputs, f"{dimension.key} reports no inputs"

    def test_every_dimension_has_a_slot_id(self) -> None:
        grade = compute_grade(_fm(2), _two_routes(), [], None)
        for dimension in grade.dimensions:
            assert dimension.explanation_slot_id == f"grade_{dimension.key}"

    def test_ungraded_dimensions_explain_why(self) -> None:
        grade = compute_grade(_fm(1), {"R1": _rm("R1")}, [], None)
        for dimension in grade.dimensions:
            if dimension.not_graded:
                assert dimension.inputs.get("reason")

    def test_violation_inputs_recompute_from_route_metrics(self) -> None:
        metrics = {"R1": _rm("R1", violations=3, stops_with_windows=25)}
        compliance = _dims(compute_grade(_fm(1), metrics, [], None))["compliance"]
        violations = sum(m.time_window_violations for m in metrics.values())
        windows = sum(m.stops_with_windows for m in metrics.values())
        assert compliance.inputs["violations"] == violations
        assert compliance.inputs["stops_with_windows"] == windows
        assert compliance.inputs["violation_rate_pct"] == pytest.approx(100 * violations / windows)


class TestEmptyFleet:
    def test_no_routes_grades_nothing_without_erroring(self) -> None:
        grade = compute_grade(_fm(0), {}, [], None)
        assert grade.overall.score is None
        assert grade.overall.letter is None
        assert all(d.not_graded for d in grade.dimensions)


class TestCvHandlesNonFinite:
    """An unreachable leg makes a route's time inf; the CV must not crash the
    whole grade — statistics.stdev raises on inf/nan rather than skipping it."""

    def test_inf_values_are_dropped_not_crashed(self) -> None:
        # Two finite, one inf: the CV is computed over the finite pair.
        assert _cv([100.0, 200.0, float("inf")]) == pytest.approx(_cv([100.0, 200.0]))

    def test_nan_values_are_dropped(self) -> None:
        assert _cv([100.0, 200.0, float("nan")]) == pytest.approx(_cv([100.0, 200.0]))

    def test_too_few_finite_values_returns_none(self) -> None:
        assert _cv([float("inf"), 100.0]) is None  # only one finite left
        assert _cv([float("inf"), float("inf")]) is None

    def test_all_finite_unchanged(self) -> None:
        assert _cv([100.0, 200.0, 300.0]) is not None
