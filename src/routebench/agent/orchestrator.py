"""Analysis orchestrator — Claude-powered analysis tool selection loop."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from routebench.agent.client import LLMClient
from routebench.agent.tool_specs import build_tool_specs
from routebench.analysis.benchmark.budget import fleet_time_limit_s, route_time_limit_s
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
from routebench.core.industry import get_profile
from routebench.core.schemas import Fleet
from routebench.infra.matrix.base import MatrixProvider, MatrixResult
from routebench.infra.telemetry import Telemetry

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

MAX_TURNS = 12

# The analysis stage owns this slice of the overall progress bar; the pipeline
# reports 15% on entry and 45% on exit.
_ANALYZE_START_PCT = 15
_ANALYZE_END_PCT = 45

# Nominal cost of a non-benchmark analyzer, in seconds. They are effectively
# instant next to the solvers, but a weight of zero would make them invisible on
# the bar — six of them would advance it not at all, and the user would watch a
# stalled bar through the fast half of the run.
_FAST_TOOL_SECONDS = 1.0

# Names are wire identifiers; these are what a person should read.
_TOOL_LABELS: dict[str, str] = {
    "analyze_sequencing": "Checking stop order",
    "analyze_time_pressure": "Checking time pressure",
    "analyze_outliers": "Looking for outliers",
    "analyze_territory": "Comparing territories",
    "analyze_dispatch": "Checking dispatch balance",
    "analyze_compliance": "Checking shift compliance",
    "analyze_reachability": "Checking for unreachable stops",
    "analyze_service_sanity": "Checking service times",
    "route_benchmark": "Re-solving each route",
    "fleet_benchmark": "Re-solving the whole fleet",
}


def _tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name.replace("_", " ").capitalize())


# (percent_complete, human detail, seconds of solver budget left or None)
ProgressReporter = Callable[[int, str, int | None], None]


def _humanize_seconds(seconds: float) -> str:
    """A rough duration a person can read: '4m 30s', '45s'."""
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    return f"{minutes}m" if secs == 0 else f"{minutes}m {secs}s"


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
        llm_available: bool | None = None,
        on_progress: ProgressReporter | None = None,
    ) -> None:
        self._client = client
        # Synchronous by design: this class runs in a worker thread, so it
        # cannot await. The pipeline supplies a shim that marshals each call
        # back onto the event loop.
        self._on_progress = on_progress
        # Whether to use the LLM for this run. Defaults to whether the client
        # could reach the API at all; a caller passes False to withhold it for a
        # reason the client cannot know — a spent daily budget, most obviously.
        # Never True when the client itself is unusable: an override cannot
        # conjure an API key.
        self._llm_available = (
            client.available if llm_available is None else (llm_available and client.available)
        )
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

        # Step 3: choose which of the applicable tools to run.
        findings: list[Finding] = []
        analyses_run: list[str] = []
        # Tools solve once and deposit their structured benchmarks here, so the
        # report can render them without re-running the solvers.
        benchmark_sink: dict[str, object] = {}

        # Without an LLM, run every applicable tool.
        #
        # The LLM's only job in this loop is SELECTION — it picks from
        # `available_tools`, which applicability_check has already filtered, and
        # calls analysis_complete to stop. It contributes no content: findings
        # come from the tools, which are deterministic, and its text never
        # reaches the report. So the honest substitute for "no LLM" is not a
        # degraded analysis, it is the exhaustive one.
        #
        # What is lost is economy, not substance. Selection lets the model skip
        # benchmark tools it judges unhelpful, and those run OR-Tools solvers to
        # a configured time limit. Running all of them is slower and arguably
        # more complete.
        if not self._llm_available:
            logger.info(
                "deterministic_tool_selection",
                reason="no LLM available; running every applicable tool",
                tools=[t.name for t in available_tools],
            )
            self._run_tools_deterministically(
                available_tools,
                fleet,
                per_route_matrices,
                benchmark_sink,
                findings,
                analyses_run,
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
                    n_findings = self._invoke_tool(
                        TOOLS[tool_name],
                        fleet,
                        per_route_matrices,
                        benchmark_sink,
                        findings,
                        analyses_run,
                    )
                    if n_findings is None:
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
                                "content": f"{tool_name}: {n_findings} findings",
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

    def _expected_seconds(self, tool: AnalysisTool, fleet: Fleet) -> float:
        """How long this tool will take, in seconds.

        Not a guess for the two that matter. OR-Tools' guided local search runs
        until its configured time limit rather than stopping when it converges,
        so a benchmark tool spends its budget in full every time — which makes
        this a schedule, not an estimate. The per-route limit is paid once per
        route; the fleet limit once.
        """
        if getattr(tool, "requires_fleet_matrix", False):
            total_stops = sum(len(r.stops) for r in fleet.routes)
            return float(fleet_time_limit_s(total_stops, self._config))
        if getattr(tool, "is_benchmark", False):
            return float(sum(route_time_limit_s(len(r.stops), self._config) for r in fleet.routes))
        return _FAST_TOOL_SECONDS

    def _report(self, pct: int, detail: str, seconds_remaining: int | None = None) -> None:
        if self._on_progress is None:
            return
        self._on_progress(pct, detail, seconds_remaining)

    def _run_tools_deterministically(
        self,
        tools: list[AnalysisTool],
        fleet: Fleet,
        per_route_matrices: dict[str, MatrixResult],
        benchmark_sink: dict[str, object],
        findings: list[Finding],
        analyses_run: list[str],
    ) -> None:
        """Run every tool in order, reporting progress as it goes.

        The bar advances by expected DURATION rather than by tool count. Six of
        these eight finish in milliseconds and two run for minutes, so counting
        tools would race the bar to 75% and then park it there for the whole
        wait — the precise impression the progress work exists to remove.
        """
        costs = [self._expected_seconds(t, fleet) for t in tools]
        total = sum(costs) or 1.0
        spent = 0.0

        def solver_seconds_from(index: int) -> int | None:
            """Solver budget still ahead, counting from `index` onward.

            None once no benchmark remains: the fast analyzers finish in
            milliseconds and vary, so a countdown over them would be an
            invention. Better to show nothing than a number we made up.
            """
            remaining = sum(
                c
                for i, (t, c) in enumerate(zip(tools, costs, strict=True))
                if i >= index and getattr(t, "is_benchmark", False)
            )
            return round(remaining) if remaining > 0 else None

        def pct_at(seconds_spent: float) -> int:
            span = _ANALYZE_END_PCT - _ANALYZE_START_PCT
            return _ANALYZE_START_PCT + int(span * (seconds_spent / total))

        for i, (tool, cost) in enumerate(zip(tools, costs, strict=True)):
            detail = _tool_label(tool.name)
            if getattr(tool, "is_benchmark", False):
                detail += f" — up to {_humanize_seconds(cost)}"
            self._report(pct_at(spent), detail, solver_seconds_from(i))

            self._invoke_tool(
                tool, fleet, per_route_matrices, benchmark_sink, findings, analyses_run
            )

            spent += cost
            n = len(findings)
            self._report(
                pct_at(spent),
                f"{n} finding{'' if n == 1 else 's'} so far",
                solver_seconds_from(i + 1),
            )

    def _invoke_tool(
        self,
        tool: AnalysisTool,
        fleet: Fleet,
        per_route_matrices: dict[str, MatrixResult],
        benchmark_sink: dict[str, object],
        findings: list[Finding],
        analyses_run: list[str],
    ) -> int | None:
        """Run one analysis tool, appending its findings. None on failure.

        Shared by the LLM-driven loop and the deterministic path so the two
        cannot drift: a tool that gets its time limit or fleet matrix in one
        path but not the other would produce different results depending on
        whether an API key happened to be configured.
        """
        tool_kwargs: dict[str, Any] = {
            "matrices": per_route_matrices,
            "matrix_provider": self._matrix_provider,
            "work_rules": self._config.work_rules,
            "traffic_profile": self._config.traffic,
            "benchmark_sink": benchmark_sink,
            # The active industry profile (or None) — used by the service-sanity
            # check to judge dwell times against the vertical's plausible band.
            "industry_profile": get_profile(self._config.industry),
        }
        if getattr(tool, "is_benchmark", False):
            # Hand the config to the benchmark tools so each derives an adaptive,
            # size-scaled solve budget per route / per fleet. time_limit_s stays
            # as the non-adaptive fallback for callers that do not pass config.
            tool_kwargs["analysis_config"] = self._config
            tool_kwargs["time_limit_s"] = (
                self._config.fleet_benchmark_time_limit_s
                if getattr(tool, "requires_fleet_matrix", False)
                else self._config.route_benchmark_time_limit_s
            )
        if getattr(tool, "requires_fleet_matrix", False):
            tool_kwargs["combined_matrix"] = self._fleet_matrix(fleet)

        try:
            tool_findings = tool.run(fleet, **tool_kwargs)
        except Exception:
            logger.exception("tool_execution_error", tool=tool.name)
            return None

        findings.extend(tool_findings)
        analyses_run.append(tool.name)
        return len(tool_findings)

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
                weights=(
                    self._config.grading_weights.as_dict()
                    if self._config.grading_weights is not None
                    else None
                ),
            )

        return AnalysisReport(
            fleet=fleet,
            fleet_metrics=fleet_metrics,
            route_metrics=route_metrics,
            findings=findings,
            grade=grade,
            matrix_approximate=self._matrix_approximate,
            llm_assisted=self._llm_available,
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
