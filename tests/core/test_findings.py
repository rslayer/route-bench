"""Tests for core/findings.py — Finding types and stable hashing."""

from __future__ import annotations

from routebench.core.findings import (
    Finding,
    FindingEvidence,
    FindingReference,
)


class TestFindingComputeId:
    """Tests for Finding.compute_id() stability and collision resistance."""

    def _make_finding(
        self,
        category: str = "sequencing",
        route_ids: list[str] | None = None,
        metric_name: str = "sequencing_index",
        actual_value: float = 1.45,
    ) -> Finding:
        return Finding(
            category=category,  # type: ignore[arg-type]
            severity="medium",
            confidence=0.9,
            title="Test finding",
            evidence=[
                FindingEvidence(
                    metric_name=metric_name,
                    actual_value=actual_value,
                    comparison_value=1.3,
                    comparison_type="threshold",
                    unit="ratio",
                )
            ],
            references=FindingReference(route_ids=route_ids or ["R001"]),
            hypothesis="Test hypothesis",
            suggested_investigation="Investigate",
        )

    def test_id_is_stable(self) -> None:
        """Same inputs produce the same finding_id."""
        f1 = self._make_finding()
        f2 = self._make_finding()
        assert f1.finding_id == f2.finding_id
        assert f1.finding_id != ""

    def test_id_set_on_construction(self) -> None:
        """finding_id is computed automatically on construction."""
        f = self._make_finding()
        assert len(f.finding_id) == 16

    def test_different_category_different_id(self) -> None:
        """Different categories produce different IDs."""
        f1 = self._make_finding(category="sequencing")
        f2 = self._make_finding(category="territory")
        assert f1.finding_id != f2.finding_id

    def test_different_route_different_id(self) -> None:
        """Different route references produce different IDs."""
        f1 = self._make_finding(route_ids=["R001"])
        f2 = self._make_finding(route_ids=["R002"])
        assert f1.finding_id != f2.finding_id

    def test_different_evidence_different_id(self) -> None:
        """Different evidence values produce different IDs."""
        f1 = self._make_finding(actual_value=1.45)
        f2 = self._make_finding(actual_value=2.00)
        assert f1.finding_id != f2.finding_id

    def test_different_metric_name_different_id(self) -> None:
        """Different metric names produce different IDs."""
        f1 = self._make_finding(metric_name="sequencing_index")
        f2 = self._make_finding(metric_name="capacity_utilization")
        assert f1.finding_id != f2.finding_id

    def test_route_id_order_does_not_matter(self) -> None:
        """Route IDs are sorted before hashing, so order doesn't matter."""
        f1 = self._make_finding(route_ids=["R001", "R002"])
        f2 = self._make_finding(route_ids=["R002", "R001"])
        assert f1.finding_id == f2.finding_id

    def test_compute_id_matches_finding_id(self) -> None:
        """compute_id() returns the same value as the auto-set finding_id."""
        f = self._make_finding()
        assert f.compute_id() == f.finding_id
