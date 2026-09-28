from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class HealthCheckResponse(BaseModel):
    """Health check status returned by the FastAPI backend."""

    status: str
    service: str
    version: str
    environment: str
    timestamp: datetime


class ReadinessResponse(BaseModel):
    """Readiness probe status returned by the FastAPI backend."""

    status: str
    database: bool
    redis: bool
    timestamp: datetime


class ItemRead(BaseModel):
    """Representation of an Item fetched from the FastAPI backend."""

    id: UUID
    title: str
    description: str | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ItemCreate(BaseModel):
    """Payload to create a new Item via the FastAPI backend."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool = True


class PaginatedResponse[T](BaseModel):
    """Generic envelope matching the backend's RFC-compliant pagination format."""

    items: list[T]
    total: int
    page: int
    size: int
    pages: int


class AICitation(BaseModel):
    """Citation or web source reference returned by the AI provider."""

    title: str
    url: str
    snippet: str | None = None


class AIChatRequest(BaseModel):
    """Request payload to query the backend AI chat endpoint."""

    prompt: str = Field(min_length=1, max_length=10000)
    conversation_id: str | None = None
    model: str = "deepseek-v3"
    search_enabled: bool = False
    file_ids: list[str] | None = None


class AIChatResponse(BaseModel):
    """Response payload returned by the backend AI chat endpoint."""

    conversation_id: str
    response: str
    model: str
    search_enabled: bool = False
    file_ids: list[str] = Field(default_factory=list)
    citations: list[AICitation] = Field(default_factory=list)


class AIResetResponse(BaseModel):
    """Response payload returned after resetting an AI conversation context."""

    conversation_id: str
    reset: bool
