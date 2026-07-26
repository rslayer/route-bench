"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routebench.agent.client import LLMClient
from routebench.analysis.visuals.maps import set_default_tile_source
from routebench.app.api.admin import router as admin_router
from routebench.app.api.routes import router as session_router
from routebench.app.budget import BudgetTracker
from routebench.app.pipeline import PipelineDeps
from routebench.app.retention import RetentionJob
from routebench.app.sessions import SessionRegistry
from routebench.app.telemetry_sink import TelemetrySink
from routebench.app.worker import SessionWorker
from routebench.core.config import Settings
from routebench.core.version import package_version
from routebench.infra.matrix.base import MatrixProvider
from routebench.infra.matrix.fallback import FallbackMatrixProvider
from routebench.infra.matrix.haversine import HaversineMatrixProvider
from routebench.infra.matrix.osrm import OSRMMatrixProvider
from routebench.infra.matrix.perleg_cache import PerLegMatrixCache
from routebench.infra.storage.base import StorageBackend
from routebench.infra.storage.local import LocalStorageBackend
from routebench.infra.tiles import from_settings as tiles_from_settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "s3":
        from routebench.infra.storage.s3 import S3StorageBackend

        return S3StorageBackend(
            endpoint_url=settings.r2_endpoint,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
            region=settings.r2_region,
        )
    return LocalStorageBackend(base_path=settings.storage_path)


def _build_primary_matrix_provider(settings: Settings) -> MatrixProvider:
    """Select the primary matrix engine from config, mirroring `_build_storage`.

    "google" is imported lazily so a deployment that never uses it does not pay
    the import, and an operator who selects it without a key fails at startup —
    a clear configuration error — rather than 403-ing on the first matrix call
    partway through an analysis.
    """
    if settings.matrix_engine == "google":
        from routebench.infra.matrix.google import GoogleMatrixProvider

        if not settings.google_maps_api_key:
            raise ValueError(
                "matrix_engine is 'google' but GOOGLE_MAPS_API_KEY is not set. "
                "Set the key or switch matrix_engine back to 'osrm'."
            )
        return GoogleMatrixProvider(api_key=settings.google_maps_api_key)
    return OSRMMatrixProvider(host=settings.osrm_host)


def _configure_logging(log_level: str) -> None:
    """Configure structlog with JSON output for production, console for dev."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        # Render exc_info into a readable traceback string so logger.exception()
        # actually shows the traceback in JSON logs, not just an exc_info flag.
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if level <= logging.DEBUG:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[*shared_processors, renderer],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = Settings()

    _configure_logging(settings.log_level)

    # Decide the basemap once, at startup, rather than per render. Off unless
    # configured, so no deployment makes outbound tile calls without saying so.
    tile_source = tiles_from_settings(settings)
    set_default_tile_source(tile_source)
    logger.info("basemap_configured", tile_source=tile_source.name, enabled=tile_source.enabled)

    storage = _build_storage(settings)
    # The primary engine (OSRM or Google) is the real source of road-network
    # travel times, but it must not be a single point of failure: unwrapped, an
    # unreachable engine meant every upload was accepted with a 202 and then
    # died at 15%. The haversine fallback keeps the site answering with
    # clearly-labelled estimates, and the pipeline withholds the quality grade
    # when it sees them.
    #
    # The cache wraps the primary and sits *inside* the fallback, not outside
    # it. That ordering matters: a cache above the fallback would happily store
    # haversine estimates, so a brief engine outage would serve them for the
    # cache lifetime long after the engine recovered. Here, an engine failure
    # raises straight through the cache to the fallback and nothing approximate
    # is ever written.
    primary = _build_primary_matrix_provider(settings)
    if settings.matrix_cache_enabled:
        # Per-leg cache: reuses individual origin->destination legs across fleets,
        # not just whole-matrix re-runs, so a second fleet in a warm region pays
        # Google only for the legs it introduces. Sits inside the fallback for the
        # same reason the whole-matrix cache did — an engine outage raises through
        # it and no estimate is ever stored.
        primary = PerLegMatrixCache(
            backend=primary,
            cache_dir=Path(settings.matrix_cache_path),
            snap_decimals=settings.matrix_cache_snap_decimals,
            ttl_seconds=settings.matrix_cache_ttl_days * 86_400,
        )
    matrix_provider = FallbackMatrixProvider(
        primary=primary,
        fallback=HaversineMatrixProvider(),
    )
    logger.info(
        "matrix_provider_configured",
        provider=matrix_provider.name,
        engine=settings.matrix_engine,
        cache_enabled=settings.matrix_cache_enabled,
    )
    llm_client = LLMClient(api_key=settings.anthropic_api_key, model=settings.claude_model)

    # Built before deps: the pipeline consults it per run to decide whether the
    # LLM may be used, so an exhausted budget degrades the analysis instead of
    # rejecting the upload.
    budget_tracker = BudgetTracker(
        storage=storage,
        daily_budget_usd=settings.daily_budget_usd,
    )
    # A parallel tracker for matrix-engine spend, on its own ledger so it never
    # mixes with LLM spend. A no-op unless the engine is metered (Google) and a
    # cap is configured; the pipeline consults it per run.
    matrix_budget_tracker = BudgetTracker(
        storage=storage,
        daily_budget_usd=settings.daily_matrix_budget_usd,
        ledger_prefix="matrix-ledger",
    )

    deps = PipelineDeps(
        matrix_provider=matrix_provider,
        storage=storage,
        llm_client=llm_client,
        settings=settings,
        budget_tracker=budget_tracker,
        matrix_budget_tracker=matrix_budget_tracker,
    )

    registry = SessionRegistry(storage=storage)
    telemetry_sink = TelemetrySink(storage=storage)
    worker = SessionWorker(
        deps=deps,
        registry=registry,
        max_queue_depth=settings.max_queue_depth,
        job_timeout_seconds=settings.job_timeout_seconds,
        telemetry_sink=telemetry_sink,
        budget_tracker=budget_tracker,
    )
    retention_job = RetentionJob(
        storage=storage,
        session_ttl_hours=settings.session_ttl_hours,
        telemetry_ttl_hours=settings.telemetry_ttl_hours,
        job_timeout_seconds=settings.job_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker.start()
        retention_job.start()
        logger.info("worker_and_retention_started")

        if settings.sentry_dsn:
            try:
                import sentry_sdk

                sentry_sdk.init(
                    dsn=settings.sentry_dsn,
                    traces_sample_rate=0.1,
                    send_default_pii=False,
                )
                logger.info("sentry_initialized")
            except Exception:
                logger.exception("sentry_init_failed")

        yield

        await worker.stop()
        await retention_job.stop()
        logger.info("worker_and_retention_stopped")

    app = FastAPI(
        title="RouteBench",
        description="Route benchmarking API",
        version=package_version(),
        lifespan=lifespan,
    )

    # CORS for the standalone web app, which is a separate origin. Off by
    # default: with no WEB_ORIGIN set this is same-origin only, as it was.
    # Explicit allowlist, never "*" — session artifacts are protected only by an
    # unguessable URL, so a wildcard would let any page that learns a session id
    # read its report.
    origins = settings.web_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )
        logger.info("cors_enabled", origins=origins)

    # Rate limiting.
    #
    # Reuse the limiter the route decorators are bound to. Constructing a second
    # one here left two instances: the decorators enforced against theirs, while
    # app.state held a different object that nothing consulted — so tuning or
    # disabling app.state.limiter silently did nothing. slowapi's exception
    # handler expects to find the real one on app.state.
    from slowapi.errors import RateLimitExceeded

    from routebench.app.api.routes import limiter

    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: object, exc: RateLimitExceeded) -> object:
        from fastapi.responses import JSONResponse

        logger.warning("rate_limited", detail=str(exc))
        # Tell the client how long to back off. slowapi only emits Retry-After
        # when headers_enabled=True, which also injects headers into successful
        # pydantic responses and turns them into 500s — so we derive it here on
        # the error path only. get_expiry() is the window length in seconds
        # (3600 for "10/hour"); fall back to 60 if the shape ever changes.
        retry_after = 60
        try:
            if exc.limit is not None:
                retry_after = int(exc.limit.limit.get_expiry())
        except Exception:
            retry_after = 60
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Try again later."},
            headers={"Retry-After": str(retry_after)},
        )

    # Store deps on app state
    app.state.settings = settings
    app.state.storage = storage
    app.state.registry = registry
    app.state.worker = worker
    app.state.deps = deps
    app.state.telemetry_sink = telemetry_sink
    app.state.budget_tracker = budget_tracker
    app.state.retention_job = retention_job

    # Register routes
    app.include_router(session_router)
    app.include_router(admin_router)

    # Serve sample reports as static files
    samples_dir = Path(__file__).parent.parent.parent.parent / "data" / "samples"
    if samples_dir.exists():
        app.mount("/samples", StaticFiles(directory=str(samples_dir)), name="samples")

    return app
