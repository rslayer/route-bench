"""Analysis orchestrator — Claude-powered analysis tool selection loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from routebench.agent.client import LLMClient
from routebench.agent.tool_specs import build_tool_specs
from routebench.analysis.benchmark.fleet_matrix import get_fleet_matrix
from routebench.analysis.scoring import compute_scorecard
from routebench.analysis.scoring.distance import get_route_matrix
from routebench.analysis.scoring.grading import compute_grade
from routebench.analysis.tools import TOOLS, AnalysisTool
from routebench.core.config import AnalysisConfig
from routebench.core.findings import (
    AnalysisReport,
    BenchmarkResult,
    Finding,
    FleetMetrics,
    RouteMetrics,
)
from routebench.core.schemas import Fleet
from routebench.infra.matrix.base import MatrixProvider, MatrixResult
from routebench.infra.telemetry import Telemetry

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

MAX_TURNS = 12

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text()


def _build_fleet_summary(fleet: Fleet) -> str:
    """Build a text summary of the fleet for the LLM."""
    total_stops = sum(len(r.stops) for r in fleet.routes)
    depot = fleet.routes[0] if fleet.routes else None
    depot_str = f"({depot.depot_lat:.4f}, {depot.depot_lon:.4f})" if depot else "N/A"

    has_demand = any(s.demand_units is not None for r in fleet.routes for s in r.stops)
    has_time_windows = any(s.time_window_start is not None for r in fleet.routes for s in r.stops)
    has_capacity = any(r.vehicle_capacity_units is not None for r in fleet.routes)

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
        telemetry: Telemetry | None = None,
    ) -> None:
        self._client = client
        self._config = config or AnalysisConfig()
        self._matrix_provider = matrix_provider
        self._telemetry = telemetry
        self._fleet_matrix_cache: MatrixResult | None = None
        # Set once the matrices are fetched. Recorded on the instance rather than
        # threaded as a parameter because _build_report has two call sites and a
        # flag that only reaches one of them is worse than no flag at all.
        self._matrix_approximate = False

    def _fleet_matrix(self, fleet: Fleet) -> MatrixResult:
        """Combined fleet matrix, built on first use and reused thereafter.

        Spans every stop in the fleet, so it is a far larger fetch than the
        per-route matrices. Built lazily because it is wasted work unless the
        orchestrator actually calls the fleet benchmark.
        """
        if self._fleet_matrix_cache is None:
            if self._matrix_provider is None:
                msg = "matrix_provider is required for the fleet benchmark"
                raise ValueError(msg)
            self._fleet_matrix_cache = get_fleet_matrix(
                fleet,
                self._matrix_provider,
                self._config.work_rules,
            )
        return self._fleet_matrix_cache

    @staticmethod
    def _assemble_benchmark(sink: dict[str, object]) -> BenchmarkResult | None:
        """Build BenchmarkResult from whatever the benchmark tools deposited."""
        per_route = sink.get("per_route") or {}
        fleet_level = sink.get("fleet_level")
        if not per_route and fleet_level is None:
            return None
        return BenchmarkResult(
            per_route=per_route,  # type: ignore[arg-type]
            fleet_level=fleet_level,  # type: ignore[arg-type]
        )

    def run(self, fleet: Fleet) -> AnalysisReport:
        """Run the full analysis pipeline."""
        # Step 1: Always compute scorecard first
        if self._matrix_provider is None:
            msg = "matrix_provider is required for analysis"
            raise ValueError(msg)
        fleet_metrics, route_metrics = compute_scorecard(
            fleet,
            self._matrix_provider,
            self._config,
        )

        # Step 1b: Pre-compute per-route matrices for tool use
        per_route_matrices: dict[str, MatrixResult] = {}
        for route in fleet.routes:
            per_route_matrices[route.route_id] = get_route_matrix(
                route,
                self._matrix_provider,
                self._config.work_rules,
            )

        # One estimated route taints the whole analysis: fleet-level metrics sum
        # across routes, so a grade computed from a mix of measured and guessed
        # times would be neither. `any` is the honest reduction.
        self._matrix_approximate = any(m.approximate for m in per_route_matrices.values())

        # Step 2: Filter tools by applicability
        available_tools: list[AnalysisTool] = []
        skipped: list[tuple[str, str]] = []
        for tool in TOOLS.values():
            if getattr(tool, "is_benchmark", False) and not self._config.include_benchmark:
                skipped.append((tool.name, "benchmarking disabled by include_benchmark"))
                continue
            check = tool.applicability_check(fleet)
            if check.is_applicable:
                available_tools.append(tool)
            else:
                skipped.append((tool.name, check.reason))

        # Step 3: LLM-driven tool selection loop
        findings: list[Finding] = []
        analyses_run: list[str] = []
        # Tools solve once and deposit their structured benchmarks here, so the
        # report can render them without re-running the solvers.
        benchmark_sink: dict[str, object] = {}
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
                        fleet,
                        fleet_metrics,
                        route_metrics,
                        findings,
                        analyses_run,
                        skipped,
                        self._assemble_benchmark(benchmark_sink),
                    )

                if tool_name in TOOLS:
                    tool = TOOLS[tool_name]
                    try:
                        tool_kwargs: dict[str, Any] = {
                            "matrices": per_route_matrices,
                            "matrix_provider": self._matrix_provider,
                            "work_rules": self._config.work_rules,
                            "traffic_profile": self._config.traffic,
                            "benchmark_sink": benchmark_sink,
                        }
                        if getattr(tool, "is_benchmark", False):
                            # Solvers spend their limit in full, so these must
                            # come from config rather than the tools' defaults.
                            tool_kwargs["time_limit_s"] = (
                                self._config.fleet_benchmark_time_limit_s
                                if getattr(tool, "requires_fleet_matrix", False)
                                else self._config.route_benchmark_time_limit_s
                            )
                        if getattr(tool, "requires_fleet_matrix", False):
                            tool_kwargs["combined_matrix"] = self._fleet_matrix(fleet)

                        tool_findings = tool.run(fleet, **tool_kwargs)
                        findings.extend(tool_findings)
                        analyses_run.append(tool_name)

                        summary = f"{tool_name}: {len(tool_findings)} findings"
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": summary,
                            }
                        )
                    except Exception:
                        logger.exception("tool_execution_error", tool=tool_name)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": f"Error running {tool_name}",
                                "is_error": True,
                            }
                        )
                else:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": f"Unknown tool: {tool_name}",
                            "is_error": True,
                        }
                    )

            # Add assistant message and tool results to conversation
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        return self._build_report(
            fleet,
            fleet_metrics,
            route_metrics,
            findings,
            analyses_run,
            skipped,
            self._assemble_benchmark(benchmark_sink),
        )

    def _build_report(
        self,
        fleet: Fleet,
        fleet_metrics: FleetMetrics,
        route_metrics: dict[str, RouteMetrics],
        findings: list[Finding],
        analyses_run: list[str],
        analyses_skipped: list[tuple[str, str]],
        benchmark: BenchmarkResult | None = None,
    ) -> AnalysisReport:
        # Graded last: the rubric reads findings (territory overlap) and the
        # benchmark, so it must run after both exist.
        #
        # Withheld entirely when travel times are straight-line estimates. Every
        # dimension of the grade is a function of time or distance, so grading an
        # approximate matrix would produce a letter that looks exactly as
        # authoritative as a real one and is not. The rest of the report — routes,
        # map, findings — still stands on its own and is still returned.
        if self._matrix_approximate:
            grade = None
            logger.warning(
                "grade_withheld_approximate_matrix",
                reason="travel times are straight-line estimates; routing backend was unavailable",
            )
        else:
            grade = compute_grade(
                fleet_metrics=fleet_metrics,
                route_metrics=route_metrics,
                findings=findings,
                benchmark=benchmark,
            )

        return AnalysisReport(
            fleet=fleet,
            fleet_metrics=fleet_metrics,
            route_metrics=route_metrics,
            findings=findings,
            grade=grade,
            matrix_approximate=self._matrix_approximate,
            benchmark=benchmark,
            analyses_run=analyses_run,
            analyses_skipped=analyses_skipped,
            metadata={
                "orchestrator_model": self._client._model,
                # Persisted to analysis.json so the report always discloses the
                # clock it was graded on, including on admin replay.
                "traffic_profile": self._traffic_metadata(),
                **(
                    {
                        "telemetry_summary": self._telemetry.summary(),
                    }
                    if self._telemetry
                    else {}
                ),
            },
        )

    def _traffic_metadata(self) -> dict[str, object]:
        """Describe the traffic profile the analysis was graded under."""
        profile = self._config.traffic
        return {
            "active": profile.is_active,
            "profile_hash": profile.profile_hash(),
            "default_factor": profile.default_factor,
            "bands": [
                {
                    "start": band.start.strftime("%H:%M"),
                    "end": band.end.strftime("%H:%M"),
                    "speed_factor": band.speed_factor,
                }
                for band in sorted(profile.bands, key=lambda b: b.start)
            ],
        }
