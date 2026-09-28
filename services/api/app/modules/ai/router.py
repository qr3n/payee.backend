from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.modules.ai.schemas import (
    AIChatRequest,
    AIChatResponse,
    AIFileMetadata,
    AIResetResponse,
)
from app.modules.ai.service import BaseAIService, get_ai_service

router = APIRouter(prefix="/ai", tags=["AI & LLM"])


@router.post(
    "/chat",
    response_model=AIChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Send chat prompt to AI",
    description=(
        "Generates a stateful chat completion using the configured AI provider "
        "(DeepSeek Stateful Proxy or Mock). Supports conversation context memory, "
        "real-time internet search, and multimodal file attachments."
    ),
)
async def chat_completion(
    request: AIChatRequest,
    ai_service: BaseAIService = Depends(get_ai_service),
) -> AIChatResponse:
    """Generate stateful AI response maintaining conversation context."""
    return await ai_service.chat(
        prompt=request.prompt,
        conversation_id=request.conversation_id,
        model=request.model,
        search_enabled=request.search_enabled,
        file_ids=request.file_ids,
    )


@router.post(
    "/files",
    response_model=AIFileMetadata,
    status_code=status.HTTP_201_CREATED,
    summary="Upload file for AI conversation",
    description=(
        "Uploads a document or image to be attached to a specific AI conversation."
    ),
)
async def upload_file(
    conversation_id: str = Form(
        ..., description="Conversation ID to attach the file to"
    ),
    file: UploadFile = File(..., description="File to upload"),
    ai_service: BaseAIService = Depends(get_ai_service),
) -> AIFileMetadata:
    """Upload an attachment for multimodal conversation understanding."""
    content = await file.read()
    filename = file.filename or "upload.bin"
    content_type = file.content_type or "application/octet-stream"

    return await ai_service.upload_file(
        conversation_id=conversation_id,
        filename=filename,
        content=content,
        content_type=content_type,
    )


@router.get(
    "/conversations/{conversation_id}/files",
    response_model=list[AIFileMetadata],
    summary="List conversation files",
    description="Retrieve list of files attached to a specific conversation.",
)
async def list_conversation_files(
    conversation_id: str,
    ai_service: BaseAIService = Depends(get_ai_service),
) -> list[AIFileMetadata]:
    """Retrieve all files associated with a conversation."""
    return await ai_service.list_files(conversation_id=conversation_id)


@router.delete(
    "/conversations/{conversation_id}",
    response_model=AIResetResponse,
    summary="Reset conversation context",
    description=(
        "Clears server-side conversation memory, starting a fresh conversation session."
    ),
)
async def reset_conversation(
    conversation_id: str,
    ai_service: BaseAIService = Depends(get_ai_service),
) -> AIResetResponse:
    """Reset dialog history and start fresh context."""
    return await ai_service.reset_conversation(conversation_id=conversation_id)
