import uuid
from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog
from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.exceptions import AppException
from app.modules.ai.schemas import (
    AIChatResponse,
    AICitation,
    AIFileMetadata,
    AIResetResponse,
)

logger = structlog.stdlib.get_logger(__name__)


class AIServiceError(AppException):
    """Exception raised when an AI service operation fails."""

    def __init__(self, message: str, code: str = "AI_SERVICE_ERROR") -> None:
        super().__init__(message=message, status_code=502, code=code)


class BaseAIService(ABC):
    """Abstract interface defining operations for stateful AI & LLM interaction.

    This abstraction allows the application to remain provider-agnostic,
    enabling seamless switching between DeepSeek Stateful Wrapper, official
    OpenAI-compatible APIs, Anthropic, or local offline mocks.
    """

    @abstractmethod
    async def chat(
        self,
        prompt: str,
        conversation_id: str | None = None,
        model: str = "deepseek-v3",
        search_enabled: bool = False,
        file_ids: list[str] | None = None,
    ) -> AIChatResponse:
        """Send a prompt and receive a response, maintaining conversation context."""
        ...

    @abstractmethod
    async def upload_file(
        self,
        conversation_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> AIFileMetadata:
        """Upload and attach a file or image to a conversation."""
        ...

    @abstractmethod
    async def list_files(
        self,
        conversation_id: str,
    ) -> list[AIFileMetadata]:
        """List files attached to a specific conversation."""
        ...

    @abstractmethod
    async def reset_conversation(
        self,
        conversation_id: str,
    ) -> AIResetResponse:
        """Reset and clear conversation context on the server."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release underlying network resources."""
        ...


class DeepSeekProxyService(BaseAIService):
    """Implementation of BaseAIService targeting the DeepSeek Stateful Proxy.

    Communicates with the stateful DeepSeek wrapper to leverage free web-session
    chat completions with multi-turn server-side memory, file understanding,
    and internet search capabilities.
    """

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        prompt: str,
        conversation_id: str | None = None,
        model: str = "deepseek-v3",
        search_enabled: bool = False,
        file_ids: list[str] | None = None,
    ) -> AIChatResponse:
        conv_id = conversation_id or str(uuid.uuid4())
        payload: dict[str, Any] = {
            "model": model,
            "conversation_id": conv_id,
            "search_enabled": search_enabled,
            "messages": [{"role": "user", "content": prompt}],
        }
        # Strip empty/whitespace IDs that Swagger UI sends as placeholder "string"
        valid_file_ids = [fid for fid in (file_ids or []) if fid and fid.strip()]
        if valid_file_ids:
            payload["file_ids"] = valid_file_ids

        client = await self._get_client()
        try:
            response = await client.post("/v1/chat/completions", json=payload)
            # If proxy returned 502 and we had file_ids, retry without them.
            # Handles invalid / expired file IDs (e.g. Swagger placeholder "string").
            if response.status_code == 502 and valid_file_ids:
                logger.warning(
                    "deepseek_proxy_file_ids_caused_502_retrying_without",
                    file_ids=valid_file_ids,
                )
                payload_no_files = {k: v for k, v in payload.items() if k != "file_ids"}
                response = await client.post(
                    "/v1/chat/completions", json=payload_no_files
                )
            if response.status_code >= 400:
                error_detail = response.text
                logger.error(
                    "deepseek_proxy_chat_failed",
                    status_code=response.status_code,
                    detail=error_detail,
                )
                raise AIServiceError(
                    f"DeepSeek proxy HTTP {response.status_code}: {error_detail}"
                )
            data = response.json()
        except httpx.RequestError as exc:
            logger.error("deepseek_proxy_connection_error", error=str(exc))
            raise AIServiceError(
                f"Failed to communicate with DeepSeek proxy: {exc}"
            ) from exc

        choices = data.get("choices", [])
        if not choices:
            raise AIServiceError("DeepSeek proxy returned an empty choice set")

        content = choices[0].get("message", {}).get("content", "").strip()
        raw_citations = data.get("citations", [])
        citations = [
            AICitation(
                title=c.get("title", "Source"),
                url=c.get("url", ""),
                snippet=c.get("snippet"),
            )
            for c in raw_citations
            if isinstance(c, dict)
        ]

        return AIChatResponse(
            conversation_id=data.get("conversation_id", conv_id),
            response=content,
            model=data.get("model", model),
            search_enabled=data.get("search_enabled", search_enabled),
            file_ids=data.get("file_ids", file_ids or []),
            citations=citations,
        )

    async def upload_file(
        self,
        conversation_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> AIFileMetadata:
        client = await self._get_client()
        files = {"file": (filename, content, content_type)}
        try:
            response = await client.post(
                "/v1/files",
                params={"conversation_id": conversation_id},
                files=files,
            )
            if response.status_code >= 400:
                raise AIServiceError(
                    f"File upload HTTP {response.status_code}: {response.text}"
                )
            data = response.json()
            return AIFileMetadata(
                id=str(data.get("id", "")),
                filename=str(data.get("filename", data.get("file_name", filename))),
                model_kind=data.get("model_kind"),
            )
        except httpx.RequestError as exc:
            raise AIServiceError(f"Upload request to proxy failed: {exc}") from exc

    async def list_files(
        self,
        conversation_id: str,
    ) -> list[AIFileMetadata]:
        client = await self._get_client()
        try:
            response = await client.get(f"/v1/conversations/{conversation_id}/files")
            if response.status_code >= 400:
                raise AIServiceError(
                    f"List files HTTP {response.status_code}: {response.text}"
                )
            data = response.json()
            raw_files = data.get("files", [])
            return [
                AIFileMetadata(
                    id=str(f.get("id", "")),
                    filename=str(f.get("filename", f.get("file_name", "file"))),
                    model_kind=f.get("model_kind"),
                )
                for f in raw_files
            ]
        except httpx.RequestError as exc:
            raise AIServiceError(f"List files request to proxy failed: {exc}") from exc

    async def reset_conversation(
        self,
        conversation_id: str,
    ) -> AIResetResponse:
        client = await self._get_client()
        try:
            response = await client.delete(f"/v1/conversations/{conversation_id}")
            if response.status_code >= 400:
                raise AIServiceError(
                    f"Reset conversation HTTP {response.status_code}: {response.text}"
                )
            data = response.json()
            return AIResetResponse(
                conversation_id=conversation_id,
                reset=bool(data.get("reset", True)),
            )
        except httpx.RequestError as exc:
            raise AIServiceError(f"Reset request to proxy failed: {exc}") from exc


class MockAIService(BaseAIService):
    """Deterministic in-memory mock service for automated testing and offline dev."""

    def __init__(self) -> None:
        self.conversations: dict[str, list[dict[str, str]]] = {}
        self.files: dict[str, list[AIFileMetadata]] = {}

    async def close(self) -> None:
        pass

    async def chat(
        self,
        prompt: str,
        conversation_id: str | None = None,
        model: str = "deepseek-v3",
        search_enabled: bool = False,
        file_ids: list[str] | None = None,
    ) -> AIChatResponse:
        conv_id = conversation_id or str(uuid.uuid4())
        history = self.conversations.setdefault(conv_id, [])
        history.append({"role": "user", "content": prompt})

        turn_count = sum(1 for m in history if m["role"] == "user")
        response_text = (
            f"[Mock DeepSeek response for turn {turn_count}]: "
            f"Processed prompt '{prompt[:50]}...' with model {model}."
        )
        history.append({"role": "assistant", "content": response_text})

        citations = []
        if search_enabled:
            citations.append(
                AICitation(
                    title="Mock Knowledge Base",
                    url="https://example.com/kb",
                    snippet="Simulated search context snippet",
                )
            )

        return AIChatResponse(
            conversation_id=conv_id,
            response=response_text,
            model=model,
            search_enabled=search_enabled,
            file_ids=file_ids or [],
            citations=citations,
        )

    async def upload_file(
        self,
        conversation_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> AIFileMetadata:
        _ = content
        file_id = f"mock-file-{uuid.uuid4().hex[:8]}"
        is_image = "image" in content_type
        metadata = AIFileMetadata(
            id=file_id,
            filename=filename,
            model_kind="VISION" if is_image else "NORMAL",
        )
        self.files.setdefault(conversation_id, []).append(metadata)
        return metadata

    async def list_files(
        self,
        conversation_id: str,
    ) -> list[AIFileMetadata]:
        return self.files.get(conversation_id, [])

    async def reset_conversation(
        self,
        conversation_id: str,
    ) -> AIResetResponse:
        self.conversations.pop(conversation_id, None)
        self.files.pop(conversation_id, None)
        return AIResetResponse(conversation_id=conversation_id, reset=True)


def get_ai_service(
    settings: Settings = Depends(get_settings),
) -> BaseAIService:
    """Dependency provider injecting the active AI service implementation.

    Controlled by Settings.AI_PROVIDER ('deepseek' vs 'mock').
    """
    if settings.AI_PROVIDER == "mock":
        return MockAIService()

    return DeepSeekProxyService(
        base_url=settings.DEEPSEEK_PROXY_URL,
        timeout=settings.DEEPSEEK_TIMEOUT,
    )
