from pydantic import BaseModel, Field


class AICitation(BaseModel):
    """Citation or web source returned when search is enabled."""

    title: str = Field(description="Title of the source webpage or document")
    url: str = Field(description="URL to the cited source")
    snippet: str | None = Field(
        default=None, description="Relevant text excerpt from source"
    )


class AIChatRequest(BaseModel):
    """Request payload for stateful LLM chat completion."""

    prompt: str = Field(
        min_length=1,
        description="User message or prompt for the AI model",
        examples=["Explain how quantum computing differs from classical computing."],
    )
    conversation_id: str | None = Field(
        default=None,
        description="Persistent conversation ID to continue server context",
        examples=["client-chat-12345"],
    )
    model: str = Field(
        default="deepseek-v3",
        description="Model identifier to use (e.g. deepseek-v3, deepseek-r1)",
        examples=["deepseek-v3"],
    )
    search_enabled: bool = Field(
        default=False,
        description="Whether to perform real-time internet search before responding",
    )
    file_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of uploaded file IDs to attach to this message. "
            "Obtain file IDs by calling POST /api/v1/ai/files first. "
            "Omit or pass an empty list if no files are attached."
        ),
        examples=[[]],
    )


class AIChatResponse(BaseModel):
    """Response payload for stateful LLM chat completion."""

    conversation_id: str = Field(
        description="Conversation ID for continuing the dialog"
    )
    response: str = Field(description="Assistant response text")
    model: str = Field(description="Model identifier used for generation")
    search_enabled: bool = Field(
        default=False, description="Whether internet search was used"
    )
    file_ids: list[str] = Field(default_factory=list, description="Attached file IDs")
    citations: list[AICitation] = Field(
        default_factory=list, description="Citations used during search"
    )


class AIFileMetadata(BaseModel):
    """Metadata of an uploaded file attached to a conversation."""

    id: str = Field(description="Unique file identifier assigned by proxy")
    filename: str = Field(description="Original filename")
    model_kind: str | None = Field(
        default=None,
        description="Model modality classification (e.g., VISION, NORMAL)",
    )


class AIResetResponse(BaseModel):
    """Response confirming conversation context reset."""

    conversation_id: str
    reset: bool
