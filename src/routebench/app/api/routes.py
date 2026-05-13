"""Core API routes — session CRUD, SSE events, report downloads."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from routebench.app.sessions import SessionRegistry, SessionState, SessionStatus
from routebench.app.worker import JobRequest
from routebench.core.config import AnalysisConfig
from routebench.core.validation import validate_csv

limiter = Limiter(key_func=get_remote_address)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

router = APIRouter()


class SessionCreateResponse(BaseModel):
    """Response for POST /sessions."""

    session_id: str
    status_url: str


class ConfigPayload(BaseModel):
    """Optional config override for session creation."""

    include_benchmark: bool = True
    include_pdf: bool = False
    sequencing_threshold: float = 1.30


@router.post("/sessions", status_code=202, response_model=SessionCreateResponse)
@limiter.limit("10/hour;100/day")
async def create_session(
    request: Request,
    file: UploadFile,
    config: str | None = Form(default=None),
) -> SessionCreateResponse:
    """Upload a CSV and start an analysis session."""
    worker = request.app.state.worker
    registry = request.app.state.registry

    # Check queue capacity
    if worker.is_full:
        raise HTTPException(status_code=429, detail="Queue is full. Try again later.")

    # Check daily budget (Phase 9)
    budget_tracker = getattr(request.app.state, "budget_tracker", None)
    if budget_tracker is not None and budget_tracker.is_exceeded():
        raise HTTPException(
            status_code=503,
            detail="Daily budget exceeded. Service resumes at UTC midnight.",
        )

    # Read file
    upload_data = await file.read()
    if not upload_data:
        raise HTTPException(status_code=422, detail="Empty file uploaded")

    # Parse config
    analysis_config = AnalysisConfig()
    if config:
        try:
            config_data = json.loads(config)
            analysis_config = AnalysisConfig(**config_data)
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc

    # Quick CSV validation (synchronous, cheap)
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(upload_data)
        tmp_path = Path(f.name)

    try:
        fleet, report = validate_csv(tmp_path)
        if fleet is None:
            errors = [{"code": e.code, "message": e.message} for e in report.errors]
            raise HTTPException(status_code=422, detail={"validation_errors": errors})
    finally:
        tmp_path.unlink(missing_ok=True)

    # Create session
    session_id = uuid.uuid4().hex[:16]
    registry.create(session_id)

    # Write upload to storage
    storage = request.app.state.storage
    await storage.write(session_id, "upload.csv", upload_data)

    # Enqueue job
    job = JobRequest(
        session_id=session_id,
        upload_data=upload_data,
        config=analysis_config,
    )
    enqueued = await worker.enqueue(job)
    if not enqueued:
        raise HTTPException(status_code=429, detail="Queue is full. Try again later.")

    logger.info("session_created", session_id=session_id)
    return SessionCreateResponse(
        session_id=session_id,
        status_url=f"/sessions/{session_id}",
    )


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> SessionStatus:
    """Get current session status."""
    registry: SessionRegistry = request.app.state.registry
    status = await registry.get(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return status


@router.get("/sessions/{session_id}/events")
async def session_events(request: Request, session_id: str) -> EventSourceResponse:
    """SSE stream of progress events for a session."""
    registry = request.app.state.registry

    async def event_generator() -> Any:
        last_state: SessionState | None = None
        last_pct: int = -1

        while True:
            status = await registry.get(session_id)
            if status is None:
                yield {"event": "error", "data": json.dumps({"error": "Session not found"})}
                return

            if status.state != last_state or status.progress_pct != last_pct:
                last_state = status.state
                last_pct = status.progress_pct
                yield {
                    "event": "progress",
                    "data": status.model_dump_json(),
                }

            if status.state in ("succeeded", "failed"):
                yield {
                    "event": "complete",
                    "data": status.model_dump_json(),
                }
                return

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.get("/sessions/{session_id}/report.html")
async def download_report_html(request: Request, session_id: str) -> RedirectResponse:
    """Redirect to the HTML report (pre-signed URL or local path)."""
    storage = request.app.state.storage
    if not await storage.exists(session_id, "report.html"):
        raise HTTPException(status_code=404, detail="Report not found")
    url = await storage.presigned_url(session_id, "report.html")
    return RedirectResponse(url=url, status_code=302)


@router.get("/sessions/{session_id}/report.pdf")
async def download_report_pdf(request: Request, session_id: str) -> RedirectResponse:
    """Redirect to the PDF report."""
    storage = request.app.state.storage
    if not await storage.exists(session_id, "report.pdf"):
        raise HTTPException(status_code=404, detail="PDF report not found")
    url = await storage.presigned_url(session_id, "report.pdf")
    return RedirectResponse(url=url, status_code=302)


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    """Readiness probe — checks OSRM reachable and storage writable."""
    import httpx

    settings = request.app.state.settings
    storage = request.app.state.storage

    checks: dict[str, bool] = {}

    # Check storage
    checks["storage_writable"] = await storage.is_writable()

    # Check OSRM
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.osrm_host}/table/v1/driving/-96.8,32.8;-96.7,32.9")
            checks["osrm_reachable"] = resp.status_code == 200
    except Exception:
        checks["osrm_reachable"] = False

    all_ok = all(checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )
