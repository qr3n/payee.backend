from datetime import UTC, datetime

from pydantic import BaseModel, Field


class HealthCheckResponse(BaseModel):
    status: str = Field(
        default="ok",
        description="Health status of the service",
        examples=["ok"],
    )
    service: str = Field(
        description="Name of the service",
        examples=["FastAPI Service"],
    )
    version: str = Field(
        description="Application version",
        examples=["0.1.0"],
    )
    environment: str = Field(
        description="Deployment environment (development, production, etc.)",
        examples=["development"],
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Current UTC timestamp",
    )


class ReadinessResponse(BaseModel):
    status: str = Field(
        default="ready",
        description="Readiness status ('ready' or 'unhealthy')",
        examples=["ready"],
    )
    database: bool = Field(
        description="PostgreSQL connectivity status",
        examples=[True],
    )
    redis: bool = Field(
        description="Redis connectivity status",
        examples=[True],
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Current UTC timestamp",
    )
