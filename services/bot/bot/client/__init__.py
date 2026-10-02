"""API Client package for communicating with the FastAPI backend."""

from bot.client.api import ApiClient, ApiClientError
from bot.client.schemas import (
    HealthCheckResponse,
    PaginatedResponse,
    PaymentCreate,
    PaymentRead,
    ReadinessResponse,
    ScenarioRead,
    TelegramAccountCheckResponse,
    TelegramAccountCreate,
    TelegramAccountRead,
)

__all__ = [
    "ApiClient",
    "ApiClientError",
    "HealthCheckResponse",
    "PaginatedResponse",
    "PaymentCreate",
    "PaymentRead",
    "ReadinessResponse",
    "ScenarioRead",
    "TelegramAccountCheckResponse",
    "TelegramAccountCreate",
    "TelegramAccountRead",
]
