"""AnalysisTool protocol and registry.

Tools are registered in later phases; registry is empty in Phase 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from routebench.core.findings import Finding
    from routebench.core.schemas import Fleet


class ApplicabilityResult(BaseModel):
    """Result of checking whether an analysis tool is applicable to a fleet."""

    is_applicable: bool
    reason: str


class AnalysisTool(Protocol):
    """Protocol for deterministic analysis tools."""

    name: str
    description: str
    requires_matrix: bool

    def applicability_check(self, fleet: Fleet) -> ApplicabilityResult: ...

    def run(self, fleet: Fleet, **kwargs: object) -> list[Finding]: ...


TOOLS: dict[str, AnalysisTool] = {}


def register_tool(tool: AnalysisTool) -> None:
    """Register an analysis tool in the global registry."""
    TOOLS[tool.name] = tool
