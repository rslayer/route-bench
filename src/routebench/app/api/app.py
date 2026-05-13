"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
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
from routebench.infra.matrix.osrm import OSRMMatrixProvider
from routebench.infra.storage.base import StorageBackend
from routebench.infra.storage.local import LocalStorageBackend
from routebench.infra.telemetry import Telemetry

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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = Settings()

    storage = _build_storage(settings)
    matrix_provider = OSRMMatrixProvider(host=settings.osrm_host)
    llm_client = LLMClient(api_key=settings.anthropic_api_key, model=settings.claude_model)
    telemetry = Telemetry(session_id="global")

    deps = PipelineDeps(
        matrix_provider=matrix_provider,
        storage=storage,
        llm_client=llm_client,
        telemetry=telemetry,
        settings=settings,
    )

    registry = SessionRegistry(storage=storage)
    worker = SessionWorker(
        deps=deps,
        registry=registry,
        max_queue_depth=settings.max_queue_depth,
        job_timeout_seconds=settings.job_timeout_seconds,
    )
    telemetry_sink = TelemetrySink(storage=storage)
    budget_tracker = BudgetTracker(daily_budget_usd=settings.daily_budget_usd)
    retention_job = RetentionJob(
        storage=storage,
        session_ttl_hours=settings.session_ttl_hours,
        telemetry_ttl_hours=settings.telemetry_ttl_hours,
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
        version="0.2.0",
        lifespan=lifespan,
    )

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
