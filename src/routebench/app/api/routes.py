"""Core API routes — session CRUD, SSE events, report downloads."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from routebench.app.sessions import SessionRegistry, SessionState, SessionStatus
from routebench.app.worker import JobRequest
from routebench.core.config import MAX_UPLOAD_BYTES, AnalysisConfig
from routebench.core.validation import validate_csv
from routebench.core.version import build_info
from routebench.infra.storage.local import LocalStorageBackend

# THE limiter. The @limiter.limit decorators below close over this instance at
# import time, so this is the one that actually rate-limits — app.py imports it
# rather than constructing its own, because a second Limiter on app.state would
# look authoritative while the request path never consulted it.
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
    if budget_tracker is not None and await budget_tracker.is_exceeded():
        raise HTTPException(
            status_code=503,
            detail="Daily budget exceeded. Service resumes at UTC midnight.",
        )

    # Read file with size limit
    upload_data = await file.read()
    if not upload_data:
        raise HTTPException(status_code=422, detail="Empty file uploaded")
    if len(upload_data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    # Parse config
    analysis_config = AnalysisConfig()
    if config:
        try:
            config_data = json.loads(config)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc
        # Valid JSON that is not an object — "[1,2,3]", "42", a bare string —
        # reaches AnalysisConfig(**data) and raises TypeError, which the old
        # handler did not catch, so a malformed request became a 500. Rejecting
        # the shape first keeps the error where it belongs: with the caller.
        if not isinstance(config_data, dict):
            raise HTTPException(
                status_code=422,
                detail="Invalid config: expected a JSON object",
            )
        try:
            analysis_config = AnalysisConfig(**config_data)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc

    # Quick CSV validation (synchronous, cheap)
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(upload_data)
        tmp_path = Path(f.name)

    try:
        # Pass the config through: validate_csv reads service_time to fill in
        # per-stop defaults, and without it the caller's choice was accepted,
        # persisted, echoed back, and silently ignored.
        fleet, report = validate_csv(tmp_path, analysis_config)
        if fleet is None:
            errors = [{"code": e.code, "message": e.message} for e in report.errors]
            raise HTTPException(status_code=422, detail={"validation_errors": errors})
    finally:
        tmp_path.unlink(missing_ok=True)

    # Create session with full UUID
    session_id = uuid.uuid4().hex
    registry.create(session_id)

    # Write upload to storage
    storage = request.app.state.storage
    await storage.write(session_id, "upload.csv", upload_data)

    # Persist the config and the queued status before enqueuing. Without both,
    # a restart cannot recover this session: nothing on disk would say it exists
    # (status.json is written by the worker, which may never run), and the
    # caller's config lives only in the in-memory JobRequest — recovering
    # without it would silently run a different analysis than was requested.
    await storage.write(
        session_id,
        "config.json",
        analysis_config.model_dump_json(indent=2).encode(),
    )
    await registry.persist(session_id)

    # Enqueue job — clean up on failure
    job = JobRequest(
        session_id=session_id,
        upload_data=upload_data,
        config=analysis_config,
    )
    enqueued = await worker.enqueue(job)
    if not enqueued:
        registry.remove_active(session_id)
        await storage.delete_session(session_id)
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

    max_poll_seconds = 660  # 11 minutes
    heartbeat_interval = 15  # seconds

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        last_state: SessionState | None = None
        last_pct: int = -1
        start = time.monotonic()
        last_heartbeat = start

        while time.monotonic() - start < max_poll_seconds:
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
                last_heartbeat = time.monotonic()

            if status.state in ("succeeded", "failed"):
                yield {
                    "event": "complete",
                    "data": status.model_dump_json(),
                }
                return

            # Send heartbeat to keep connection alive
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                yield {"event": "heartbeat", "data": "{}"}
                last_heartbeat = now

            await asyncio.sleep(0.5)

        yield {"event": "error", "data": json.dumps({"error": "SSE timeout"})}

    return EventSourceResponse(event_generator())


@router.get("/sessions/{session_id}/report.html", response_model=None)
async def download_report_html(request: Request, session_id: str) -> RedirectResponse | Response:
    """Redirect to the HTML report (pre-signed URL or serve directly for local storage)."""
    storage = request.app.state.storage
    if not await storage.exists(session_id, "report.html"):
        raise HTTPException(status_code=404, detail="Report not found")
    if isinstance(storage, LocalStorageBackend):
        data = await storage.read(session_id, "report.html")
        return Response(content=data, media_type="text/html")
    url = await storage.presigned_url(session_id, "report.html")
    return RedirectResponse(url=url, status_code=302)


@router.get("/sessions/{session_id}/report.pdf", response_model=None)
async def download_report_pdf(request: Request, session_id: str) -> RedirectResponse | Response:
    """Redirect to the PDF report (or serve directly for local storage)."""
    storage = request.app.state.storage
    if not await storage.exists(session_id, "report.pdf"):
        raise HTTPException(status_code=404, detail="PDF report not found")
    if isinstance(storage, LocalStorageBackend):
        data = await storage.read(session_id, "report.pdf")
        return Response(content=data, media_type="application/pdf")
    url = await storage.presigned_url(session_id, "report.pdf")
    return RedirectResponse(url=url, status_code=302)


@router.get("/sessions/{session_id}/analysis.json", response_model=None)
async def download_analysis_json(request: Request, session_id: str) -> RedirectResponse | Response:
    """The structured analysis — findings, metrics, benchmark.

    The UI renders from this rather than re-deriving anything: it is the same
    artifact the report was built from, so the page and the PDF cannot disagree.
    """
    storage = request.app.state.storage
    if not await storage.exists(session_id, "analysis.json"):
        raise HTTPException(status_code=404, detail="Analysis not found")
    if isinstance(storage, LocalStorageBackend):
        data = await storage.read(session_id, "analysis.json")
        return Response(content=data, media_type="application/json")
    url = await storage.presigned_url(session_id, "analysis.json")
    return RedirectResponse(url=url, status_code=302)


@router.get("/sessions/{session_id}/routes.geojson", response_model=None)
async def download_routes_geojson(request: Request, session_id: str) -> RedirectResponse | Response:
    """The map artifact — route lines, stops, depots, migration arrows.

    Geometry is approximate (straight segments between stops, not driven road
    paths); the collection's `geometry_approximate` property says so and the UI
    is expected to surface it.
    """
    storage = request.app.state.storage
    if not await storage.exists(session_id, "routes.geojson"):
        raise HTTPException(status_code=404, detail="Map data not found")
    if isinstance(storage, LocalStorageBackend):
        data = await storage.read(session_id, "routes.geojson")
        return Response(content=data, media_type="application/geo+json")
    url = await storage.presigned_url(session_id, "routes.geojson")
    return RedirectResponse(url=url, status_code=302)


@router.get("/health")
async def health() -> JSONResponse:
    """Build identity for the web footer: `v{X.Y.Z} · build {short_sha}`.

    Deliberately dependency-free and always 200 — this answers "what is
    deployed", not "is it working". /healthz is the readiness probe that checks
    OSRM and storage, and a footer must not go blank because OSRM is down.
    """
    return JSONResponse(status_code=200, content=build_info())


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    """Readiness probe — checks OSRM reachable and storage writable."""
    import httpx

    settings = request.app.state.settings
    storage = request.app.state.storage

    checks: dict[str, bool] = {}

    # Check storage
    checks["storage_writable"] = await storage.is_writable()

    # Check OSRM with a lightweight nearest query
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.osrm_host}/nearest/v1/driving/0,0")
            checks["osrm_reachable"] = resp.status_code == 200
    except Exception:
        checks["osrm_reachable"] = False

    all_ok = all(checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )
