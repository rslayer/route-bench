"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routebench.agent.client import LLMClient
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
from routebench.infra.matrix.osrm import OSRMMatrixProvider
from routebench.infra.storage.base import StorageBackend
from routebench.infra.storage.local import LocalStorageBackend

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


def _configure_logging(log_level: str) -> None:
    """Configure structlog with JSON output for production, console for dev."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
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
    storage = _build_storage(settings)
    matrix_provider = OSRMMatrixProvider(host=settings.osrm_host)
    llm_client = LLMClient(api_key=settings.anthropic_api_key, model=settings.claude_model)

    deps = PipelineDeps(
        matrix_provider=matrix_provider,
        storage=storage,
        llm_client=llm_client,
        settings=settings,
    )

    registry = SessionRegistry(storage=storage)
    telemetry_sink = TelemetrySink(storage=storage)
    budget_tracker = BudgetTracker(
        storage=storage,
        daily_budget_usd=settings.daily_budget_usd,
    )
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

    # Rate limiting
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: object, exc: RateLimitExceeded) -> object:
        from fastapi.responses import JSONResponse

        logger.warning("rate_limited", detail=str(exc))
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Try again later."},
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
    from pathlib import Path

    samples_dir = Path(__file__).parent.parent.parent.parent / "data" / "samples"
    if samples_dir.exists():
        app.mount("/samples", StaticFiles(directory=str(samples_dir)), name="samples")

    return app
