"""API Client package for communicating with the FastAPI backend."""

from bot.client.api import ApiClient, ApiClientError
from bot.client.schemas import (
    HealthCheckResponse,
    ItemCreate,
    ItemRead,
    PaginatedResponse,
    ReadinessResponse,
)

__all__ = [
    "ApiClient",
    "ApiClientError",
    "HealthCheckResponse",
    "ItemCreate",
    "ItemRead",
    "PaginatedResponse",
    "ReadinessResponse",
]
