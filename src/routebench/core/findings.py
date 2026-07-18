"""Finding types for RouteBench.

Defines Finding, FindingEvidence, AnalysisReport and related types.
The finding_id is computed deterministically from (category, references, evidence)
via a stable hash function.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Imported at runtime, not under TYPE_CHECKING: AnalysisReport.fleet needs Fleet
# resolvable when the class is built, or the model stays "not fully defined"
# until something happens to call model_rebuild(). core.schemas imports nothing
# from this module, so there is no cycle to dodge.
from routebench.core.schemas import Fleet

FindingCategory = Literal[
    "sequencing",
    "time_pressure",
    "utilization",
    "compliance",
    "territory",
    "dispatch",
    "outlier",
]
FindingSeverity = Literal["info", "low", "medium", "high", "critical"]
ComparisonType = Literal["fleet_median", "threshold", "optimal", "peer", None]


class FindingEvidence(BaseModel):
    """A single piece of metric evidence supporting a finding."""

    metric_name: str
    actual_value: float
    comparison_value: float | None = None
    comparison_type: ComparisonType = None
    unit: str


class FindingReference(BaseModel):
    """References to specific routes and stops related to a finding."""

    route_ids: list[str] = Field(default_factory=list)
    stop_sequences: list[tuple[str, int]] = Field(default_factory=list)


class Finding(BaseModel):
    """A structured finding from deterministic analysis."""

    finding_id: str = ""
    category: FindingCategory
    severity: FindingSeverity
    confidence: float = Field(ge=0, le=1)
    title: str
    evidence: list[FindingEvidence]
    references: FindingReference
    hypothesis: str
    suggested_investigation: str
    related_finding_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _set_finding_id(self) -> Finding:
        """Compute finding_id deterministically from category, references, and evidence."""
        if not self.finding_id:
            self.finding_id = self.compute_id()
        return self

    def compute_id(self) -> str:
        """Compute a stable hash from (category, references, evidence)."""
        payload = {
            "category": self.category,
            "route_ids": sorted(self.references.route_ids),
            "stop_sequences": sorted((r, s) for r, s in self.references.stop_sequences),
            "evidence": [
                {
                    "metric_name": e.metric_name,
                    "actual_value": round(e.actual_value, 6),
                    "comparison_value": (
                        round(e.comparison_value, 6) if e.comparison_value is not None else None
                    ),
                    "unit": e.unit,
                }
                for e in sorted(self.evidence, key=lambda x: x.metric_name)
            ],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RouteMetrics(BaseModel):
    """Per-route descriptive metrics."""

    route_id: str
    total_distance_miles: float
    total_time_hours: float
    drive_time_hours: float
    service_time_hours: float
    idle_time_hours: float
    stop_count: int
    stops_per_hour: float
    # Grading reads this for the density dimension. It was computed and dropped
    # before, which left the rubric with no artifact-recomputable input.
    stops_per_mile: float = 0.0
    sequencing_index: float | None = None
    capacity_utilization: dict[str, float] = Field(default_factory=dict)
    time_window_violations: int = 0
    # Denominator for the violation rate: a fleet with no windows cannot have a
    # violation rate, and dividing by total stops would silently score it 100%.
    stops_with_windows: int = 0
    shift_overrun_minutes: float = 0.0
    # Grading reads this for operational compliance. Also computed and dropped.
    lunch_taken_within_window: bool = True


class FleetMetrics(BaseModel):
    """Fleet-level aggregate metrics."""

    total_routes: int
    total_stops: int
    total_distance_miles: float
    total_time_hours: float
    median_sequencing_index: float | None = None
    routes_over_shift_cap: int
    avg_capacity_utilization: dict[str, float] = Field(default_factory=dict)


class StopMigration(BaseModel):
    """A stop reassigned from one route to another by the optimizer."""

    route_id: str
    stop_sequence: int
    customer_id: str | None
    from_route: str
    to_route: str


class RouteBenchmark(BaseModel):
    """Per-route benchmark comparison: plan vs the best solution the solver found.

    Gap fields are percentages (0-100) and may be negative: a negative gap means
    the solver found nothing better than the plan, which is a real result, not an
    error. See `improvement_gap_pct`.
    """

    route_id: str
    actual_distance_miles: float
    optimal_distance_miles: float
    distance_gap_pct: float
    actual_time_hours: float
    optimal_time_hours: float
    time_gap_pct: float
    # (actual - solver_solution) / actual, as a percentage. This is how much the
    # solver improved on the plan — NOT a proven distance from the true optimum.
    # The solver is a time-limited metaheuristic, so the real waste is at least
    # this figure.
    improvement_gap_pct: float
    # The solver's tour as matrix indices (1..n; 0 is the depot), in visit order.
    # Without this the report can say a plan is 12% worse than the solver's tour
    # but never show which tour, so no consumer can draw or act on it. Empty
    # when the solver produced no reorderable sequence (0- and 1-stop routes).
    stop_order: list[int] = Field(default_factory=list)


class FleetBenchmark(BaseModel):
    """Fleet-level benchmark comparison: plan vs the best solution the solver found."""

    actual_total_distance: float
    optimal_total_distance: float
    stop_migrations: list[StopMigration]
    # See RouteBenchmark.improvement_gap_pct — improvement over the plan, as a
    # percentage, not a proven optimality bound. May be negative.
    improvement_gap_pct: float


class BenchmarkResult(BaseModel):
    """Combined benchmark results.

    `fleet_level` is absent when the per-route benchmark ran but the fleet-level
    VRPTW did not — it is skipped for single-route fleets, fleets whose routes do
    not share one depot, and fleets above the solver's stop cap.
    """

    per_route: dict[str, RouteBenchmark] = Field(default_factory=dict)
    fleet_level: FleetBenchmark | None = None


class OverallGrade(BaseModel):
    """The composite score. None when nothing could be graded."""

    score: float | None = None
    letter: str | None = None


class DimensionGrade(BaseModel):
    """One graded dimension.

    `basis` records what the score was anchored to, because the same dimension
    degrades gracefully rather than disappearing: sequencing falls back from
    "benchmark" to "heuristic", fleet to "balance_only", compliance to
    "operational_only". A reader needs to know which they got.

    Every `inputs` value must be recomputable from metrics elsewhere in the
    artifact — that is the explainability guarantee. Do not put a number here
    that cannot be checked against route_metrics or benchmark.
    """

    key: str
    label: str
    score: float | None = None
    letter: str | None = None
    basis: str
    not_graded: bool = False
    inputs: dict[str, object] = Field(default_factory=dict)
    explanation_slot_id: str = ""


class Grade(BaseModel):
    """A fleet's quality score, decomposed.

    `grading_version` is load-bearing: a rubric change must never silently
    reinterpret an old report, so reports display the version they were graded
    under and the sample-fleet snapshot fails CI until the version is bumped
    deliberately.
    """

    grading_version: str
    overall: OverallGrade
    weights: dict[str, float] = Field(default_factory=dict)
    dimensions: list[DimensionGrade] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    """Complete analysis output: metrics, findings, and optional benchmark."""

    fleet: Fleet
    fleet_metrics: FleetMetrics
    route_metrics: dict[str, RouteMetrics]
    findings: list[Finding]
    benchmark: BenchmarkResult | None = None
    # The quality score. Optional so an artifact written before Phase 10.6 still
    # loads (admin replay reads old analysis.json files).
    grade: Grade | None = None
    # True when travel times came from the straight-line fallback rather than the
    # road network, because the routing backend was unreachable. The routes, map,
    # and relative findings are still worth showing; the grade is not, so it is
    # withheld and this flag is what lets the report and UI say why. Stored as a
    # fact rather than a sentence: the wording belongs at the rendering edge, not
    # baked into a persisted artifact.
    matrix_approximate: bool = False
    analyses_run: list[str]
    analyses_skipped: list[tuple[str, str]]
    metadata: dict[str, object]
