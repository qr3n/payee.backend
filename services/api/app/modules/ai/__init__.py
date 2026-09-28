"""AI & LLM module providing abstract stateful completion services."""

from app.modules.ai.router import router
from app.modules.ai.schemas import (
    AIChatRequest,
    AIChatResponse,
    AICitation,
    AIFileMetadata,
    AIResetResponse,
)
from app.modules.ai.service import (
    AIServiceError,
    BaseAIService,
    DeepSeekProxyService,
    MockAIService,
    get_ai_service,
)

__all__ = [
    "AIChatRequest",
    "AIChatResponse",
    "AICitation",
    "AIFileMetadata",
    "AIResetResponse",
    "AIServiceError",
    "BaseAIService",
    "DeepSeekProxyService",
    "MockAIService",
    "get_ai_service",
    "router",
]
