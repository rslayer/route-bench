"""Finding types for RouteBench.

Defines Finding, FindingEvidence, AnalysisReport and related types.
The finding_id is computed deterministically from (category, references, evidence)
via a stable hash function.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
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
            "stop_sequences": sorted(
                (r, s) for r, s in self.references.stop_sequences
            ),
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
    sequencing_index: float | None = None
    capacity_utilization: dict[str, float] = Field(default_factory=dict)
    time_window_violations: int = 0
    shift_overrun_minutes: float = 0.0


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
    """Per-route benchmark comparison: actual vs optimal."""

    route_id: str
    actual_distance_miles: float
    optimal_distance_miles: float
    distance_gap_pct: float
    actual_time_hours: float
    optimal_time_hours: float
    time_gap_pct: float
    optimality_gap_reported_by_solver: float


class FleetBenchmark(BaseModel):
    """Fleet-level benchmark comparison."""

    actual_total_distance: float
    optimal_total_distance: float
    stop_migrations: list[StopMigration]
    optimality_gap_reported_by_solver: float


class BenchmarkResult(BaseModel):
    """Combined benchmark results."""

    per_route: dict[str, RouteBenchmark]
    fleet_level: FleetBenchmark


class AnalysisReport(BaseModel):
    """Complete analysis output: metrics, findings, and optional benchmark."""

    fleet: Fleet
    fleet_metrics: FleetMetrics
    route_metrics: dict[str, RouteMetrics]
    findings: list[Finding]
    benchmark: BenchmarkResult | None = None
    analyses_run: list[str]
    analyses_skipped: list[tuple[str, str]]
    metadata: dict[str, object]
