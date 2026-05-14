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
from routebench.agent.verifier import Verifier
from routebench.agent.writer import ReportWriter
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
from routebench.infra.matrix.base import MatrixProvider
from routebench.infra.storage.base import StorageBackend
from routebench.infra.telemetry import Telemetry
from routebench.report.document import ReportDocument
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


@dataclass
class SessionResult:
    """Result of a pipeline run."""

    session_id: str
    state: SessionState
    artifacts: SessionArtifacts | None = None
    cost: CostSummary | None = None
    error_message: str | None = None


ProgressCallback = Callable[[SessionState, int, str], Awaitable[None]]


async def run_session(
    session_id: str,
    upload_path: Path,
    config: AnalysisConfig,
    deps: PipelineDeps,
    telemetry: Telemetry | None = None,
    on_progress: ProgressCallback | None = None,
) -> SessionResult:
    """Run the full pipeline: validate -> orchestrate -> write -> verify -> render -> persist."""

    async def _emit(state: SessionState, pct: int, detail: str) -> None:
        if on_progress is not None:
            await on_progress(state, pct, detail)

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
        fleet, report = validate_csv(upload_path)
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
        orchestrator = AnalysisOrchestrator(
            client=deps.llm_client,
            config=config,
            matrix_provider=deps.matrix_provider,
            telemetry=session_telemetry,
        )
        analysis = await asyncio.to_thread(orchestrator.run, fleet)
        _check_token_cap()
        await _emit("analyzing", 45, f"Analysis complete: {len(analysis.findings)} findings")

        # Stage 3: Write prose
        await _emit("writing", 50, "Generating report prose")
        doc = ReportDocument(analysis)
        slots = identify_prose_slots(analysis)
        writer = ReportWriter(client=deps.llm_client)
        prose = await asyncio.to_thread(writer.fill_slots, slots)
        _check_token_cap()
        await _emit("writing", 65, f"Prose generated for {len(prose)} slots")

        # Stage 4: Verify prose
        await _emit("writing", 70, "Verifying prose against findings")
        verifier = Verifier(client=deps.llm_client)

        def _writer_fn(s: ProseSlot) -> str:
            return writer.fill_slots([s]).get(s.slot_id, "")

        verified_result = await asyncio.to_thread(
            verifier.verify_and_regenerate, prose, slots, _writer_fn
        )
        verified_prose = verified_result[0]
        await _emit("writing", 75, "Prose verified")

        # Stage 5: Render HTML
        await _emit("rendering", 80, "Rendering HTML report")
        html = doc.render(verified_prose)
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
