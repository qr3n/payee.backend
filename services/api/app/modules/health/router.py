from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_db, get_redis
from app.core.config import settings
from app.modules.health.schemas import HealthCheckResponse, ReadinessResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Service Health Check",
    description="Returns the status, service name, version, and current environment.",
)
async def check_health() -> HealthCheckResponse:
    """Check health and operational status of the service."""
    return HealthCheckResponse(
        status="ok",
        service=settings.PROJECT_NAME,
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    summary="Service Readiness Probe",
    description="Validates active connectivity to both PostgreSQL and Redis.",
)
async def check_readiness(
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ReadinessResponse:
    """Readiness probe: validates database and Redis connections."""
    db_healthy = False
    redis_healthy = False

    try:
        result = await db.exec(select(1))
        db_healthy = result.first() == 1
    except Exception:
        db_healthy = False

    try:
        redis_healthy = bool(await redis.ping())
    except Exception:
        redis_healthy = False

    is_ready = db_healthy and redis_healthy
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if is_ready else "unhealthy",
        database=db_healthy,
        redis=redis_healthy,
    )
