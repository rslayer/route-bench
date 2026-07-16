"""Admin API endpoints — gated by ADMIN_TOKEN header."""

from __future__ import annotations

import asyncio
import hmac
from datetime import datetime

import structlog
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from routebench.app.sessions import SessionStatus

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

router = APIRouter(prefix="/admin")


def _check_token(request: Request, x_admin_token: str = Header(...)) -> None:
    expected = request.app.state.settings.admin_token
    if not expected or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")


class SessionListResponse(BaseModel):
    """Paginated session list."""

    sessions: list[SessionStatus]
    total: int


class CostDistribution(BaseModel):
    """Cost distribution stats."""

    count: float
    p50: float
    p95: float
    max: float
    total: float
    budget_rejections: int = 0


class ReplayResponse(BaseModel):
    """Response for replay endpoint."""

    session_id: str
    status: str


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    request: Request,
    x_admin_token: str = Header(...),
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> SessionListResponse:
    """List sessions with cost summary."""
    _check_token(request, x_admin_token)
    registry = request.app.state.registry

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as err:
            raise HTTPException(status_code=422, detail="Invalid 'since' datetime format") from err

    all_sessions = await registry.list_all(since=since_dt)
    total = len(all_sessions)
    paginated = all_sessions[offset : offset + limit]

    return SessionListResponse(sessions=paginated, total=total)


@router.get("/costs", response_model=CostDistribution)
async def cost_distribution(
    request: Request,
    x_admin_token: str = Header(...),
    window: int = 24,
) -> CostDistribution:
    """Aggregated cost-per-session distribution."""
    _check_token(request, x_admin_token)

    telemetry_sink = getattr(request.app.state, "telemetry_sink", None)
    if telemetry_sink is None:
        return CostDistribution(count=0, p50=0.0, p95=0.0, max=0.0, total=0.0)

    dist = telemetry_sink.cost_distribution(window_hours=window)

    budget_tracker = getattr(request.app.state, "budget_tracker", None)
    rejections = budget_tracker.rejections if budget_tracker is not None else 0

    return CostDistribution(**dist, budget_rejections=rejections)


@router.post("/sessions/{session_id}/replay", response_model=ReplayResponse)
async def replay_session(
    request: Request,
    session_id: str,
    x_admin_token: str = Header(...),
) -> ReplayResponse:
    """Re-render a stored analysis.json without re-running the pipeline."""
    _check_token(request, x_admin_token)

    storage = request.app.state.storage

    # Load analysis.json
    try:
        analysis_data = await storage.read(session_id, "analysis.json")
    except FileNotFoundError as err:
        raise HTTPException(
            status_code=404, detail="analysis.json not found for this session"
        ) from err

    from routebench.agent.client import LLMClient
    from routebench.agent.verifier import Verifier
    from routebench.agent.writer import ReportWriter
    from routebench.core.findings import AnalysisReport
    from routebench.report.document import ReportDocument
    from routebench.report.prose_slots import ProseSlot, identify_prose_slots

    analysis = AnalysisReport.model_validate_json(analysis_data)

    doc = ReportDocument(analysis)
    slots = identify_prose_slots(analysis)

    settings = request.app.state.settings
    client = LLMClient(api_key=settings.anthropic_api_key, model=settings.claude_model)
    writer = ReportWriter(client=client)

    def _writer_fn(s: ProseSlot) -> str:
        return writer.fill_slots([s]).get(s.slot_id, "")

    # Run LLM calls in a thread to avoid blocking the event loop
    prose = await asyncio.to_thread(writer.fill_slots, slots)
    verifier = Verifier(client=client)
    verified_prose, verification = await asyncio.to_thread(
        verifier.verify_and_regenerate, prose, slots, _writer_fn
    )

    html = doc.render(verified_prose, verification=verification)
    await storage.write(session_id, "report.html", html.encode())

    logger.info("session_replayed", session_id=session_id)
    return ReplayResponse(session_id=session_id, status="replayed")
