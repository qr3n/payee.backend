import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.v1.router import api_v1_router
from app.core.broker import broker
from app.core.config import settings
from app.core.db import async_engine
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.middleware import RequestIDMiddleware
from app.core.redis import close_redis, get_redis
from app.modules.accounts.session_pool import telegram_session_pool
from app.modules.health import HealthCheckResponse

logger = get_logger(__name__)

# Configure structured logging (JSON in production, human-readable in development)
setup_logging(
    json_format=not settings.DEBUG,
    log_level="DEBUG" if settings.DEBUG else "INFO",
)


async def _periodic_background_runner() -> None:
    """
    Periodically dispatches account health verification and overdue payments cleanup.
    Uses Redis distributed lock to prevent duplicate runs across multi-worker setups.
    """
    interval = max(30, settings.ACCOUNT_CHECK_INTERVAL_SECONDS)
    logger.info("periodic_background_runner_started", interval_seconds=interval)
    while True:
        try:
            await asyncio.sleep(interval)
            if not settings.ACCOUNT_CHECK_BACKGROUND_ENABLED:
                continue

            # Attempt Redis distributed lock
            try:
                redis = get_redis()
                lock = await redis.set(
                    "lock:periodic_accounts_check",
                    "1",
                    nx=True,
                    ex=max(20, interval - 5),
                )
                if not lock:
                    continue
            except Exception as redis_err:
                logger.debug("Redis lock check skipped", error=str(redis_err))

            # Dispatch background tasks to Taskiq worker
            try:
                from app.modules.accounts.tasks import check_all_accounts_task
                from app.modules.payments.tasks import expire_overdue_payments_task

                await check_all_accounts_task.kiq()
                await expire_overdue_payments_task.kiq()
            except Exception as task_err:
                logger.error(
                    "Failed dispatching periodic background tasks", error=str(task_err)
                )

        except asyncio.CancelledError:
            logger.info("periodic_background_runner_stopped")
            break
        except Exception as exc:
            logger.error("Error in periodic background runner loop", exc_info=exc)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    bg_task: asyncio.Task[None] | None = None
    # Initialize Taskiq broker in API process (workers manage their own lifecycle)
    if not broker.is_worker_process:
        await broker.startup()
        if settings.ACCOUNT_CHECK_BACKGROUND_ENABLED:
            bg_task = asyncio.create_task(_periodic_background_runner())
    yield
    if bg_task is not None:
        bg_task.cancel()
        with suppress(asyncio.CancelledError):
            await bg_task
    # Shutdown actions: gracefully close broker, database connections and redis pool
    if not broker.is_worker_process:
        await broker.shutdown()
    await async_engine.dispose()
    await close_redis()
    await telegram_session_pool.close_all()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# Register Request ID (Correlation ID) ASGI Middleware
app.add_middleware(RequestIDMiddleware)

# CORS Middleware setup
if settings.BACKEND_CORS_ORIGINS:
    has_wildcard = "*" in settings.BACKEND_CORS_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=not has_wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Register centralized exception handlers (RFC 9457 unified format)
register_exception_handlers(app)

# Initialize Prometheus metrics instrumentation (/metrics)
Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    excluded_handlers=[
        "/health",
        "/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
    ],
).instrument(app).expose(app, endpoint="/metrics", tags=["Metrics"])


# Root health endpoint (ideal for container healthchecks & load balancers)
@app.get(
    "/health",
    response_model=HealthCheckResponse,
    tags=["Health"],
    summary="Root Service Health Check",
    description=(
        "Quick liveness and readiness probe for load balancers and orchestrators."
    ),
)
async def root_health() -> HealthCheckResponse:
    return HealthCheckResponse(
        status="ok",
        service=settings.PROJECT_NAME,
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
    )


# Include API routers
app.include_router(api_v1_router, prefix=settings.API_V1_STR)
