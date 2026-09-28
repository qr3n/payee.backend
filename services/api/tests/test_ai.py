import json
from typing import Any

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.main import app
from app.modules.ai import (
    AIChatRequest,
    AIChatResponse,
    AIFileMetadata,
    AIResetResponse,
    AIServiceError,
    DeepSeekProxyService,
    MockAIService,
    get_ai_service,
)


@pytest.mark.asyncio
async def test_mock_ai_service_chat() -> None:
    service = MockAIService()
    try:
        response = await service.chat(
            prompt="Hello world",
            conversation_id="test-conv-1",
            search_enabled=True,
        )
        assert isinstance(response, AIChatResponse)
        assert response.conversation_id == "test-conv-1"
        assert "[Mock DeepSeek response" in response.response
        assert response.search_enabled is True
        assert len(response.citations) > 0

        # Multi-turn check
        response_2 = await service.chat(
            prompt="Follow up question",
            conversation_id="test-conv-1",
        )
        assert "turn 2" in response_2.response
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_mock_ai_service_files_and_reset() -> None:
    service = MockAIService()
    try:
        file_meta = await service.upload_file(
            conversation_id="conv-files",
            filename="test.png",
            content=b"dummy-image-content",
            content_type="image/png",
        )
        assert isinstance(file_meta, AIFileMetadata)
        assert file_meta.filename == "test.png"
        assert file_meta.model_kind == "VISION"

        files = await service.list_files("conv-files")
        assert len(files) == 1
        assert files[0].id == file_meta.id

        reset = await service.reset_conversation("conv-files")
        assert isinstance(reset, AIResetResponse)
        assert reset.reset is True

        assert len(await service.list_files("conv-files")) == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_deepseek_proxy_service_chat() -> None:
    def handler(request: Request) -> Response:
        assert request.url.path == "/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-v3"
        assert body["conversation_id"] == "c-123"
        assert body["search_enabled"] is True

        payload: dict[str, Any] = {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "DeepSeek generated answer.",
                    }
                }
            ],
            "conversation_id": "c-123",
            "model": "deepseek-v3",
            "search_enabled": True,
            "citations": [
                {
                    "title": "Example Source",
                    "url": "https://example.com",
                    "snippet": "Snippet text",
                }
            ],
        }
        return Response(200, json=payload)

    service = DeepSeekProxyService(base_url="http://mock-proxy")
    service._client = AsyncClient(
        transport=MockTransport(handler), base_url="http://mock-proxy"
    )

    try:
        result = await service.chat(
            prompt="Tell me about python",
            conversation_id="c-123",
            search_enabled=True,
        )
        assert result.response == "DeepSeek generated answer."
        assert result.conversation_id == "c-123"
        assert len(result.citations) == 1
        assert result.citations[0].url == "https://example.com"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_deepseek_proxy_service_error_handling() -> None:
    def handler(_request: Request) -> Response:
        return Response(502, text="Upstream failure")

    service = DeepSeekProxyService(base_url="http://mock-proxy")
    service._client = AsyncClient(
        transport=MockTransport(handler), base_url="http://mock-proxy"
    )

    try:
        with pytest.raises(AIServiceError) as exc_info:
            await service.chat(prompt="Fail please")
        assert "DeepSeek proxy HTTP 502" in str(exc_info.value)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_api_ai_chat_endpoint(client: AsyncClient) -> None:
    mock_service = MockAIService()
    app.dependency_overrides[get_ai_service] = lambda: mock_service

    try:
        request_data = AIChatRequest(
            prompt="Hello from API test",
            conversation_id="api-conv-1",
            search_enabled=False,
        ).model_dump()

        response = await client.post("/api/v1/ai/chat", json=request_data)
        assert response.status_code == 200
        data = response.json()
        assert data["conversation_id"] == "api-conv-1"
        assert "[Mock DeepSeek response" in data["response"]
    finally:
        app.dependency_overrides.pop(get_ai_service, None)


@pytest.mark.asyncio
async def test_api_ai_files_and_reset_endpoints(client: AsyncClient) -> None:
    mock_service = MockAIService()
    app.dependency_overrides[get_ai_service] = lambda: mock_service

    try:
        # 1. Upload file
        response = await client.post(
            "/api/v1/ai/files",
            data={"conversation_id": "api-conv-2"},
            files={"file": ("doc.txt", b"Text file content", "text/plain")},
        )
        assert response.status_code == 201
        file_data = response.json()
        assert file_data["filename"] == "doc.txt"
        assert "mock-file-" in file_data["id"]

        # 2. List files
        list_resp = await client.get("/api/v1/ai/conversations/api-conv-2/files")
        assert list_resp.status_code == 200
        files = list_resp.json()
        assert len(files) == 1
        assert files[0]["id"] == file_data["id"]

        # 3. Reset conversation
        del_resp = await client.delete("/api/v1/ai/conversations/api-conv-2")
        assert del_resp.status_code == 200
        assert del_resp.json()["reset"] is True
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
