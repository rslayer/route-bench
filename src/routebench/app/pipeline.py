"""Pipeline orchestration: CSV -> AnalysisReport -> HTML report.

Runs the full four-layer pipeline as an async function.
Each stage emits progress events via the provided callback.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from routebench.agent.client import LLMClient
from routebench.agent.orchestrator import AnalysisOrchestrator
from routebench.agent.verifier import (
    VerificationResult,
    Verifier,
    deterministic_prose,
    summarize_statuses,
)
from routebench.agent.writer import ReportWriter
from routebench.app.budget import BudgetTracker
from routebench.app.sessions import (
    CostSummary,
    SessionArtifacts,
    SessionState,
)
from routebench.core.config import (
    CLAUDE_INPUT_PRICE_PER_M,
    CLAUDE_OUTPUT_PRICE_PER_M,
    AnalysisConfig,
    Settings,
)
from routebench.core.exceptions import BudgetExceededError, RouteBenchError
from routebench.core.validation import validate_csv
from routebench.infra.geometry import OSRMGeometryProvider
from routebench.infra.matrix.base import MatrixProvider
from routebench.infra.matrix.traffic import TrafficAdjustedProvider
from routebench.infra.storage.base import StorageBackend
from routebench.infra.telemetry import Telemetry
from routebench.report.document import ReportDocument
from routebench.report.geojson import build_routes_geojson
from routebench.report.pdf import render_pdf
from routebench.report.prose_slots import ProseSlot, identify_prose_slots

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass
class PipelineDeps:
    """Injected dependencies for the pipeline."""

    matrix_provider: MatrixProvider
    storage: StorageBackend
    llm_client: LLMClient
    settings: Settings
    # Consulted once per run to decide whether the LLM may be used. Optional so
    # that callers which do not meter spend (tests, one-off scripts) need not
    # invent one.
    budget_tracker: BudgetTracker | None = None


@dataclass
class SessionResult:
    """Result of a pipeline run."""

    session_id: str
    state: SessionState
    artifacts: SessionArtifacts | None = None
    cost: CostSummary | None = None
    error_message: str | None = None


# The 4th argument is the solver budget still to be spent, or None when the
# remaining work is not time-boxed. See SessionStatus.seconds_remaining.
ProgressCallback = Callable[[SessionState, int, str, int | None], Awaitable[None]]


async def run_session(
    session_id: str,
    upload_path: Path,
    config: AnalysisConfig,
    deps: PipelineDeps,
    telemetry: Telemetry | None = None,
    on_progress: ProgressCallback | None = None,
) -> SessionResult:
    """Run the full pipeline: validate -> orchestrate -> write -> verify -> render -> persist."""

    async def _emit(
        state: SessionState, pct: int, detail: str, seconds_remaining: int | None = None
    ) -> None:
        if on_progress is not None:
            await on_progress(state, pct, detail, seconds_remaining)

    session_telemetry = telemetry or Telemetry(session_id=session_id)
    max_input_tokens = deps.settings.max_input_tokens_per_session

    def _check_token_cap() -> None:
        """Raise BudgetExceededError if cumulative input tokens exceed the cap."""
        total_input = sum(c.input_tokens for c in session_telemetry.llm_calls)
        if total_input > max_input_tokens:
            raise BudgetExceededError(
                f"Session input token cap exceeded: {total_input}/{max_input_tokens}",
                budget_type="session_input_tokens",
                limit=float(max_input_tokens),
                current=float(total_input),
            )

    try:
        # Stage 1: Validate CSV
        await _emit("validating", 5, "Validating CSV data")
        # config, not the default: service_time.default_minutes is consumed
        # here, so omitting it made the constraints panel's service-time
        # control inert.
        fleet, report = validate_csv(upload_path, config)
        if fleet is None:
            errors_json = json.dumps(
                [{"code": e.code, "message": e.message} for e in report.errors]
            )
            return SessionResult(
                session_id=session_id,
                state="failed",
                error_message=f"Validation failed: {errors_json}",
            )
        n_stops = fleet.total_stops()
        n_routes = len(fleet.routes)
        await _emit("validating", 10, f"Validated: {n_stops} stops across {n_routes} routes")

        # Stage 2: Orchestrate analysis
        await _emit("analyzing", 15, "Running analysis orchestrator")

        # The matrix provider is a process-wide singleton but the traffic profile
        # is chosen per upload, so band here rather than at the composition root.
        matrix_provider = deps.matrix_provider
        if config.traffic.is_active:
            matrix_provider = TrafficAdjustedProvider(deps.matrix_provider, config.traffic)
            logger.info(
                "traffic_profile_active",
                session_id=session_id,
                profile_hash=config.traffic.profile_hash(),
                n_bands=len(config.traffic.bands),
            )

        # Decide once whether the LLM may be used for this run, and let both the
        # orchestrator and the prose stage honour the same answer.
        #
        # Two things can take it away: no API key, and a spent daily budget.
        # The budget case used to reject the upload with a 503 for the rest of
        # the UTC day, which took the entire site down over a spend cap on the
        # one part of the analysis that is optional. The evaluation itself costs
        # nothing to produce, so it still runs; only the tool selection and the
        # narrative are lost.
        llm_available = deps.llm_client.available
        if (
            llm_available
            and deps.budget_tracker is not None
            and await deps.budget_tracker.is_exceeded()
        ):
            llm_available = False
            logger.info(
                "llm_disabled_budget_exceeded",
                session_id=session_id,
                reason="daily budget spent; running the deterministic analysis",
            )

        # Bridge the orchestrator's progress back onto the event loop.
        #
        # orchestrator.run is synchronous and runs in a worker thread, while the
        # progress callback is async — which is why this stage used to be one
        # opaque await. It reported 15% on the way in and nothing again until
        # 45%, so the longest phase of the run (the solvers, minutes of it) was
        # completely dark to the user.
        #
        # run_coroutine_threadsafe is the supported way across that boundary.
        # Fire and forget: a dropped progress tick must never break an analysis,
        # so failures are logged and swallowed rather than raised into the
        # solver thread.
        loop = asyncio.get_running_loop()

        def _emit_from_thread(pct: int, detail: str, seconds_remaining: int | None) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    _emit("analyzing", pct, detail, seconds_remaining), loop
                )
            except Exception:
                logger.warning("progress_emit_failed", session_id=session_id, detail=detail)

        orchestrator = AnalysisOrchestrator(
            client=deps.llm_client,
            config=config,
            matrix_provider=matrix_provider,
            telemetry=session_telemetry,
            llm_available=llm_available,
            on_progress=_emit_from_thread,
        )
        analysis = await asyncio.to_thread(orchestrator.run, fleet)
        _check_token_cap()
        await _emit("analyzing", 45, f"Analysis complete: {len(analysis.findings)} findings")

        # Part D: log the score distribution so the v1.0 breakpoints — which are
        # engineering judgment, not calibration — can be recalibrated once real
        # uploads accumulate. Dimension scores only: no coordinates, no route
        # ids, nothing identifying the fleet.
        if analysis.grade is not None:
            logger.info(
                "grade_distribution",
                session_id=session_id,
                grading_version=analysis.grade.grading_version,
                overall_score=analysis.grade.overall.score,
                overall_letter=analysis.grade.overall.letter,
                dimensions={
                    d.key: {"score": d.score, "basis": d.basis, "not_graded": d.not_graded}
                    for d in analysis.grade.dimensions
                },
            )

        # Stages 3 and 4: prose, then verification of it.
        #
        # Both are skipped entirely when there is no LLM. Templated prose needs
        # no verification: the verifier exists to catch a model inventing a
        # number, and a template can only restate numbers it was given. Every
        # slot is reported as "fallback", which is exactly what it is — the
        # footer then says so rather than implying prose was checked and passed.
        doc = ReportDocument(analysis)
        slots = identify_prose_slots(analysis)

        if not llm_available:
            await _emit("writing", 50, "Generating report prose (templates, no LLM)")
            verified_prose = {slot.slot_id: deterministic_prose(slot) for slot in slots}
            verification = {
                slot.slot_id: VerificationResult(
                    slot_id=slot.slot_id,
                    passed=False,
                    issues=["No LLM configured; prose filled from template"],
                    status="fallback",
                )
                for slot in slots
            }
            logger.info(
                "prose_templated",
                session_id=session_id,
                slots=len(slots),
                reason="no LLM available",
            )
            await _emit("writing", 75, f"Prose templated for {len(slots)} slots")
        else:
            await _emit("writing", 50, "Generating report prose")
            writer = ReportWriter(client=deps.llm_client)
            prose = await asyncio.to_thread(writer.fill_slots, slots)
            _check_token_cap()
            await _emit("writing", 65, f"Prose generated for {len(prose)} slots")

            await _emit("writing", 70, "Verifying prose against findings")
            verifier = Verifier(client=deps.llm_client)

            def _writer_fn(s: ProseSlot) -> str:
                return writer.fill_slots([s]).get(s.slot_id, "")

            verified_prose, verification = await asyncio.to_thread(
                verifier.verify_and_regenerate, prose, slots, _writer_fn
            )
            status_counts = summarize_statuses(verification)
            logger.info("prose_verified", session_id=session_id, **status_counts)
            await _emit(
                "writing",
                75,
                f"Prose verified: {status_counts['verified']} verified, "
                f"{status_counts['regenerated']} regenerated, "
                f"{status_counts['fallback']} deterministic fallback",
            )

        # Stage 5: Render HTML
        await _emit("rendering", 80, "Rendering HTML report")
        html = doc.render(verified_prose, verification=verification)
        await _emit("rendering", 85, "HTML report rendered")

        # Stage 5b: Render PDF if configured
        pdf_bytes: bytes | None = None
        if config.include_pdf:
            await _emit("rendering", 87, "Rendering PDF report")
            pdf_bytes = await asyncio.to_thread(render_pdf, html)

        # Stage 6: Persist artifacts
        await _emit("rendering", 90, "Saving artifacts")
        storage = deps.storage

        await storage.write(session_id, "report.html", html.encode())
        if pdf_bytes:
            await storage.write(session_id, "report.pdf", pdf_bytes)

        analysis_data = analysis.model_dump_json(indent=2).encode()
        await storage.write(session_id, "analysis.json", analysis_data)

        # Map artifact for the web UI. The UI renders geography, it never
        # computes it, so everything the map needs ships here. Geometry fetching
        # is blocking HTTP per route, hence the thread; it degrades to straight
        # lines rather than failing an analysis that already has real findings.
        geometry_provider = OSRMGeometryProvider(host=deps.settings.osrm_host)
        routes_geojson = await asyncio.to_thread(build_routes_geojson, analysis, geometry_provider)
        geojson_data = json.dumps(routes_geojson, indent=2).encode()
        await storage.write(session_id, "routes.geojson", geojson_data)

        telemetry_data = json.dumps(session_telemetry.summary(), indent=2).encode()
        await storage.write(session_id, "telemetry.json", telemetry_data)

        # Build cost summary from telemetry
        telem_summary = session_telemetry.summary()
        llm_data = telem_summary.get("llm", {})
        input_tokens = int(llm_data.get("total_input_tokens", 0))
        output_tokens = int(llm_data.get("total_output_tokens", 0))
        input_cost = input_tokens * CLAUDE_INPUT_PRICE_PER_M / 1_000_000
        output_cost = output_tokens * CLAUDE_OUTPUT_PRICE_PER_M / 1_000_000
        llm_cost = input_cost + output_cost

        cost = CostSummary(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            llm_cost_usd=round(llm_cost, 4),
            total_cost_usd=round(llm_cost, 4),
        )

        artifacts = SessionArtifacts(
            report_html="report.html",
            report_pdf="report.pdf" if pdf_bytes else "",
            analysis_json="analysis.json",
            telemetry_json="telemetry.json",
            routes_geojson="routes.geojson",
        )

        await _emit("succeeded", 100, "Report ready")

        return SessionResult(
            session_id=session_id,
            state="succeeded",
            artifacts=artifacts,
            cost=cost,
        )

    except RouteBenchError as exc:
        logger.exception("pipeline_routebench_error", session_id=session_id)
        return SessionResult(
            session_id=session_id,
            state="failed",
            error_message=str(exc),
        )
