from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """
    Detailed error representation aligned with RFC 9457 (Problem Details for HTTP APIs).
    Provides structured error taxonomy, human-readable message, and correlation ID.
    """

    code: str = Field(
        description="Machine-readable error type/code (RFC 9457 title/type)",
        examples=["ITEM_NOT_FOUND"],
    )
    message: str = Field(
        description="Human-readable description of what went wrong (RFC 9457 detail)",
        examples=["Item with specified ID does not exist."],
    )
    details: Any | None = Field(
        default=None,
        description="Optional granular error details (e.g. field validation errors)",
    )
    request_id: str | None = Field(
        default=None,
        description="Correlation Request ID for debugging (RFC 9457 instance)",
        examples=["c9b5d4a1-872f-4e0d-b8d9-a31dfb1b2299"],
    )


class ErrorResponse(BaseModel):
    """Standardized top-level error response envelope compliant with RFC 9457."""

    error: ErrorDetail
