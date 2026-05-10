"""Analysis orchestrator — Claude-powered analysis tool selection loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from routebench.agent.client import LLMClient
from routebench.agent.tool_specs import build_tool_specs
from routebench.analysis.scoring import compute_scorecard
from routebench.analysis.tools import TOOLS, AnalysisTool
from routebench.core.config import AnalysisConfig
from routebench.core.findings import (
    AnalysisReport,
    Finding,
    FleetMetrics,
    RouteMetrics,
)
from routebench.core.schemas import Fleet
from routebench.infra.matrix.base import MatrixProvider

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

MAX_TURNS = 12

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text()


def _build_fleet_summary(fleet: Fleet) -> str:
    """Build a text summary of the fleet for the LLM."""
    total_stops = sum(len(r.stops) for r in fleet.routes)
    depot = fleet.routes[0] if fleet.routes else None
    depot_str = (
        f"({depot.depot_lat:.4f}, {depot.depot_lon:.4f})"
        if depot else "N/A"
    )

    has_demand = any(
        s.demand_units is not None
        for r in fleet.routes for s in r.stops
    )
    has_time_windows = any(
        s.time_window_start is not None
        for r in fleet.routes for s in r.stops
    )
    has_capacity = any(
        r.vehicle_capacity_units is not None for r in fleet.routes
    )

    lines = [
        f"Fleet: {len(fleet.routes)} routes, {total_stops} stops",
        f"Depot: {depot_str}",
        "Data completeness:",
        f"  - Demand data: {'yes' if has_demand else 'no'}",
        f"  - Time windows: {'yes' if has_time_windows else 'no'}",
        f"  - Vehicle capacity: {'yes' if has_capacity else 'no'}",
    ]
    return "\n".join(lines)


class AnalysisOrchestrator:
    """Claude-powered orchestrator for analysis tool selection."""

    def __init__(
        self,
        client: LLMClient,
        config: AnalysisConfig | None = None,
        matrix_provider: MatrixProvider | None = None,
    ) -> None:
        self._client = client
        self._config = config or AnalysisConfig()
        self._matrix_provider = matrix_provider

    def run(self, fleet: Fleet) -> AnalysisReport:
        """Run the full analysis pipeline."""
        # Step 1: Always compute scorecard first
        if self._matrix_provider is None:
            msg = "matrix_provider is required for analysis"
            raise ValueError(msg)
        fleet_metrics, route_metrics = compute_scorecard(
            fleet, self._matrix_provider, self._config,
        )

        # Step 2: Filter tools by applicability
        available_tools: list[AnalysisTool] = []
        skipped: list[tuple[str, str]] = []
        for tool in TOOLS.values():
            check = tool.applicability_check(fleet)
            if check.is_applicable:
                available_tools.append(tool)
            else:
                skipped.append((tool.name, check.reason))

        # Step 3: LLM-driven tool selection loop
        findings: list[Finding] = []
        analyses_run: list[str] = []
        tool_specs = build_tool_specs(available_tools)

        system_prompt = _load_prompt("orchestrator")
        fleet_summary = _build_fleet_summary(fleet)

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Analyze this fleet:\n\n{fleet_summary}\n\n"
                    f"Available tools: {[t.name for t in available_tools]}\n"
                    f"Scorecard computed. Select which diagnostic/benchmark "
                    f"tools to run."
                ),
            },
        ]

        for turn in range(MAX_TURNS):
            response = self._client.generate(
                messages=messages,
                system=system_prompt,
                tools=tool_specs,
                turn_id=f"orch_turn_{turn}",
            )

            if not response.has_tool_calls:
                break

            # Process tool calls
            tool_results: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                tool_name = tc["name"]

                if tool_name == "analysis_complete":
                    logger.info(
                        "orchestrator_complete",
                        summary=tc["input"].get("summary", ""),
                        turns=turn + 1,
                    )
                    return self._build_report(
                        fleet, fleet_metrics, route_metrics,
                        findings, analyses_run, skipped,
                    )

                if tool_name in TOOLS:
                    tool = TOOLS[tool_name]
                    try:
                        tool_findings = tool.run(
                            fleet,
                            matrix_provider=self._matrix_provider,
                            work_rules=self._config.work_rules,
                        )
                        findings.extend(tool_findings)
                        analyses_run.append(tool_name)

                        summary = (
                            f"{tool_name}: {len(tool_findings)} findings"
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": summary,
                        })
                    except Exception:
                        logger.exception("tool_execution_error", tool=tool_name)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": f"Error running {tool_name}",
                            "is_error": True,
                        })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": f"Unknown tool: {tool_name}",
                        "is_error": True,
                    })

            # Add assistant message and tool results to conversation
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        return self._build_report(
            fleet, fleet_metrics, route_metrics,
            findings, analyses_run, skipped,
        )

    def _build_report(
        self,
        fleet: Fleet,
        fleet_metrics: FleetMetrics,
        route_metrics: dict[str, RouteMetrics],
        findings: list[Finding],
        analyses_run: list[str],
        analyses_skipped: list[tuple[str, str]],
    ) -> AnalysisReport:
        return AnalysisReport(
            fleet=fleet,
            fleet_metrics=fleet_metrics,
            route_metrics=route_metrics,
            findings=findings,
            benchmark=None,
            analyses_run=analyses_run,
            analyses_skipped=analyses_skipped,
            metadata={"orchestrator_model": self._client._model},
        )
