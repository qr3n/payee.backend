from typing import Any
from uuid import UUID

import httpx
import structlog

from bot.client.schemas import (
    AIChatRequest,
    AIChatResponse,
    AIResetResponse,
    HealthCheckResponse,
    ItemCreate,
    ItemRead,
    PaginatedResponse,
    ReadinessResponse,
)

logger = structlog.stdlib.get_logger(__name__)


class ApiClientError(Exception):
    """Base exception for API client errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    """Asynchronous HTTP client for interacting with the FastAPI backend.

    Treats the backend as the single source of truth for business data,
    validation, background task scheduling, and persistence.
    """

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def get_client(self) -> httpx.AsyncClient:
        """Obtain or initialize the underlying httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json", "User-Agent": "TelegramBot/1.0"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client session."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get_health(self) -> HealthCheckResponse:
        """Check basic health status of the backend API."""
        client = await self.get_client()
        response = await client.get("/health")
        response.raise_for_status()
        return HealthCheckResponse.model_validate(response.json())

    async def get_readiness(self) -> ReadinessResponse:
        """Check readiness status of backend dependencies (Postgres, Redis)."""
        client = await self.get_client()
        response = await client.get("/ready")
        response.raise_for_status()
        return ReadinessResponse.model_validate(response.json())

    async def list_items(
        self, page: int = 1, size: int = 10
    ) -> PaginatedResponse[ItemRead]:
        """Fetch a paginated list of items from the backend."""
        client = await self.get_client()
        response = await client.get(
            "/api/v1/items/", params={"page": page, "size": size}
        )
        response.raise_for_status()
        return PaginatedResponse[ItemRead].model_validate(response.json())

    async def get_item(self, item_id: str | UUID) -> ItemRead | None:
        """Retrieve a specific item by its UUID."""
        client = await self.get_client()
        response = await client.get(f"/api/v1/items/{item_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return ItemRead.model_validate(response.json())

    async def create_item(self, title: str, description: str | None = None) -> ItemRead:
        """Create a new item via the backend."""
        client = await self.get_client()
        payload = ItemCreate(title=title, description=description).model_dump(
            exclude_none=True
        )
        response = await client.post("/api/v1/items/", json=payload)
        response.raise_for_status()
        return ItemRead.model_validate(response.json())

    async def analyze_item(self, item_id: str | UUID) -> dict[str, Any]:
        """Trigger background analysis of an item via Taskiq."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/items/{item_id}/analyze")
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def ask_ai(
        self,
        prompt: str,
        conversation_id: str | None = None,
        model: str = "deepseek-v3",
        search_enabled: bool = False,
        file_ids: list[str] | None = None,
    ) -> AIChatResponse:
        """Send a prompt to the FastAPI AI chat endpoint."""
        client = await self.get_client()
        payload = AIChatRequest(
            prompt=prompt,
            conversation_id=conversation_id,
            model=model,
            search_enabled=search_enabled,
            file_ids=file_ids,
        ).model_dump(exclude_none=True)
        response = await client.post("/api/v1/ai/chat", json=payload)
        if response.status_code >= 400:
            raise ApiClientError(
                f"AI service error ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )
        return AIChatResponse.model_validate(response.json())

    async def reset_ai_conversation(self, conversation_id: str) -> AIResetResponse:
        """Reset stateful AI conversation context on the FastAPI backend."""
        client = await self.get_client()
        response = await client.delete(f"/api/v1/ai/conversations/{conversation_id}")
        if response.status_code >= 400:
            raise ApiClientError(
                f"AI reset error ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )
        return AIResetResponse.model_validate(response.json())
