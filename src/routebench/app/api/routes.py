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
from routebench.core.industry import INDUSTRY_PROFILES, IndustryProfile
from routebench.core.validation import validate_csv
from routebench.core.version import build_info
from routebench.infra.storage.local import LocalStorageBackend

# THE limiter. The @limiter.limit decorators below close over this instance at
# import time, so this is the one that actually rate-limits — app.py imports it
# rather than constructing its own, because a second Limiter on app.state would
# look authoritative while the request path never consulted it.
limiter = Limiter(key_func=get_remote_address)

# Advisory backoff (seconds) sent as Retry-After when the ingest queue is full.
# The queue is bounded and the worker is single-concurrency, so a full queue
# means a client should wait roughly one session's processing time before a slot
# frees. It is a hint, not a guarantee — the true wait depends on where the
# in-flight job sits in its solver budget.
_QUEUE_FULL_RETRY_AFTER_S = 30


def _queue_full_error() -> HTTPException:
    """429 for a full ingest queue, carrying a Retry-After backoff hint."""
    return HTTPException(
        status_code=429,
        detail="Queue is full. Try again later.",
        headers={"Retry-After": str(_QUEUE_FULL_RETRY_AFTER_S)},
    )


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
        raise _queue_full_error()

    # A spent daily budget no longer rejects the upload.
    #
    # This used to raise 503 for the remainder of the UTC day, which took the
    # whole service down over a cap on the one part of the analysis that is
    # optional. The LLM writes the narrative and picks which analyzers to run;
    # the metrics, findings, benchmark and grade are computed without it and
    # cost nothing. So an exhausted budget now degrades the run — the pipeline
    # sees it and takes the deterministic path — rather than refusing work the
    # service can still do for free.
    budget_tracker = getattr(request.app.state, "budget_tracker", None)
    if budget_tracker is not None and await budget_tracker.is_exceeded():
        logger.info("budget_exceeded_degrading", detail="running without the LLM")

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
        except (json.JSONDecodeError, RecursionError) as exc:
            # json is a recursive-descent parser, so deeply nested input (e.g.
            # 10k levels of "[[[…]]]", a few tens of KB) raises the builtin
            # RecursionError rather than JSONDecodeError — a cheap crash vector
            # that used to escape as a 500. Both mean "unparseable config" -> 422.
            raise HTTPException(status_code=422, detail="Invalid config: unparseable JSON") from exc
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
        raise _queue_full_error()

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


EXPIRED_DETAIL = (
    "This session has expired and its data has been deleted. "
    "Re-upload the file to run a fresh analysis."
)


async def _serve_artifact(
    request: Request,
    session_id: str,
    filename: str,
    *,
    media_type: str,
    missing_detail: str,
    allow_redirect: bool = True,
) -> RedirectResponse | Response:
    """Serve one session artifact, refusing expired sessions.

    Every artifact route goes through here so the expiry check cannot be
    omitted from one of them. Previously each route asked only `exists()`,
    which meant an expired session kept serving its report for as long as the
    file happened to still be on disk — and retention was not deleting the
    files at all, so that was the full telemetry TTL.

    410 rather than 404: the session did exist, and telling the holder of a
    valid link "expired" instead of "never heard of it" is both true and more
    useful. It leaks nothing they did not already know by holding the link.
    """
    storage = request.app.state.storage
    registry: SessionRegistry = request.app.state.registry

    status = await registry.get(session_id)
    if status is not None and status.state == "expired":
        raise HTTPException(status_code=410, detail=EXPIRED_DETAIL)

    if not await storage.exists(session_id, filename):
        raise HTTPException(status_code=404, detail=missing_detail)

    # Artifacts the browser fetches with JS (analysis.json, routes.geojson) must
    # be served same-origin. A 302 to a pre-signed R2 URL sends the browser to
    # storage.cloudflarestorage.com, which returns no CORS headers, so the
    # cross-origin fetch is blocked in the browser as "Failed to fetch" — even
    # though curl (which ignores CORS) sees the bytes fine. Streaming the bytes
    # back through the API keeps the response same-origin, so the app's CORS
    # middleware applies and the fetch succeeds. Reports (report.html/pdf) are
    # opened by navigation, not fetch, so they keep the redirect and avoid
    # proxying large files. Local storage always streams — there is no URL to
    # sign.
    if allow_redirect and not isinstance(storage, LocalStorageBackend):
        url = await storage.presigned_url(session_id, filename)
        return RedirectResponse(url=url, status_code=302)
    data = await storage.read(session_id, filename)
    return Response(content=data, media_type=media_type)


@router.get("/sessions/{session_id}/report.html", response_model=None)
async def download_report_html(request: Request, session_id: str) -> RedirectResponse | Response:
    """Redirect to the HTML report (pre-signed URL or serve directly for local storage)."""
    return await _serve_artifact(
        request,
        session_id,
        "report.html",
        media_type="text/html",
        missing_detail="Report not found",
    )


@router.get("/sessions/{session_id}/report.pdf", response_model=None)
async def download_report_pdf(request: Request, session_id: str) -> RedirectResponse | Response:
    """Redirect to the PDF report (or serve directly for local storage)."""
    return await _serve_artifact(
        request,
        session_id,
        "report.pdf",
        media_type="application/pdf",
        missing_detail="PDF report not found",
    )


@router.get("/sessions/{session_id}/analysis.json", response_model=None)
async def download_analysis_json(request: Request, session_id: str) -> RedirectResponse | Response:
    """The structured analysis — findings, metrics, benchmark.

    The UI renders from this rather than re-deriving anything: it is the same
    artifact the report was built from, so the page and the PDF cannot disagree.
    """
    return await _serve_artifact(
        request,
        session_id,
        "analysis.json",
        media_type="application/json",
        missing_detail="Analysis not found",
        # Fetched by the UI with JS: stream it same-origin, never redirect to R2,
        # or the browser blocks the cross-origin read (no CORS on the signed URL).
        allow_redirect=False,
    )


@router.get("/sessions/{session_id}/routes.geojson", response_model=None)
async def download_routes_geojson(request: Request, session_id: str) -> RedirectResponse | Response:
    """The map artifact — route lines, stops, depots, migration arrows.

    Geometry is approximate (straight segments between stops, not driven road
    paths); the collection's `geometry_approximate` property says so and the UI
    is expected to surface it.
    """
    return await _serve_artifact(
        request,
        session_id,
        "routes.geojson",
        media_type="application/geo+json",
        missing_detail="Map data not found",
        # Fetched by the UI with JS: stream it same-origin, never redirect to R2,
        # or the browser blocks the cross-origin read (no CORS on the signed URL).
        allow_redirect=False,
    )


@router.get("/industry-profiles", response_model=list[IndustryProfile])
async def industry_profiles() -> list[IndustryProfile]:
    """The industry benchmark presets, so the web panel applies them from one
    source of truth (core/industry.py) rather than duplicating the numbers."""
    return list(INDUSTRY_PROFILES.values())


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
    """Readiness probe — storage writability, and whether the matrix engine is serving.

    Engine-aware: what "the matrix is healthy" means depends on which engine is
    configured. Probing OSRM when the deployment runs on Google would report a
    dependency that is not even in the request path, so the check branches.
    """
    import httpx

    settings = request.app.state.settings
    storage = request.app.state.storage

    checks: dict[str, bool] = {"storage_writable": await storage.is_writable()}

    if settings.matrix_engine == "google":
        # Nothing to probe: there is no OSRM sidecar under this engine, and the
        # app would not have started without the key (the engine factory raises
        # on google-without-key), so at runtime the engine is configured. A live
        # Google call costs money per element; running one on every health check
        # — Fly hits this every 15s — would bill real money to say "up", so
        # readiness is judged on configuration, not a paid probe. A specific run
        # can still hit a quota error and fall back to haversine; that is a
        # per-run degrade, not an unready service.
        matrix_mode = "google"
        grade_available = True
    else:
        # engine == "osrm": probe it with a lightweight nearest query.
        #
        # The probe coordinate is "1,1", not "0,0": OSRM rejects the literal
        # "0,0" with HTTP 400 `{"code": "InvalidUrl"}` (its URL grammar does not
        # accept that exact string), which paired with a status==200 test made
        # healthz report degraded against a perfectly healthy OSRM.
        #
        # OSRM has no default snapping radius, so /nearest answers any
        # in-bounds-syntax coordinate with 200 by snapping to the nearest edge
        # however far away — so this proves osrm-routed is up and serving, not
        # which region is loaded. Judged on whether OSRM answered *like OSRM*:
        # every response carries a `code` field, so a parseable body proves the
        # process is serving; a connection error or a proxy's HTML page does not.
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{settings.osrm_host}/nearest/v1/driving/1,1")
                if resp.status_code == 200:
                    checks["osrm_reachable"] = True
                else:
                    body = resp.json()
                    checks["osrm_reachable"] = isinstance(body, dict) and "code" in body
        except Exception:
            checks["osrm_reachable"] = False
        matrix_mode = "osrm" if checks["osrm_reachable"] else "haversine_estimates"
        grade_available = checks["osrm_reachable"]

    all_ok = all(checks.values())
    # The status CODE gates load-balancer routing, so it must reflect only what
    # makes the service unable to serve at all — which is storage, not the matrix
    # engine. A degraded matrix (OSRM down, or Google quota-exhausted) still
    # serves haversine estimates with the grade withheld, so returning 503 for it
    # would make Fly's health check pull a working API out of rotation. Storage
    # being unreachable is different — with no place to write a session the
    # service genuinely cannot function, so that alone is a hard 503.
    #
    # The body still reports the full picture (status, matrix_mode,
    # grade_available), so the state is visible without reading logs.
    can_serve = checks["storage_writable"]
    return JSONResponse(
        content={
            "status": "ok" if all_ok else "degraded",
            "checks": checks,
            "matrix_engine": settings.matrix_engine,
            # What the deployment will actually do, not just what is up. A
            # degraded matrix is the difference between a graded report and a
            # withheld one, and that should be visible without reading logs.
            "matrix_mode": matrix_mode,
            "grade_available": grade_available,
        },
        status_code=200 if can_serve else 503,
    )
