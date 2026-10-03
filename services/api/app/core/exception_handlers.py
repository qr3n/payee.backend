import http
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.core.middleware import get_request_id

logger = get_logger(__name__)


def _build_error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    """Build unified error response compatible with RFC 9457 Problem Details."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": get_request_id() or None,
            }
        },
    )


async def app_exception_handler(_request: Request, exc: AppException) -> JSONResponse:
    """Handle custom application business exceptions."""
    return _build_error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def http_exception_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handle standard Starlette/FastAPI HTTP exceptions."""
    try:
        phrase = http.HTTPStatus(exc.status_code).phrase.upper().replace(" ", "_")
    except ValueError:
        phrase = "HTTP_ERROR"

    message = str(exc.detail) if exc.detail else phrase
    return _build_error_response(
        status_code=exc.status_code,
        code=phrase,
        message=message,
    )


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic request validation errors without leaking sensitive raw input."""
    raw_errors = jsonable_encoder(exc.errors())
    sanitized_errors = []
    for err in raw_errors:
        if isinstance(err, dict):
            cleaned = dict(err)
            cleaned.pop("input", None)
            sanitized_errors.append(cleaned)
        else:
            sanitized_errors.append(err)

    return _build_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_ERROR",
        message="Request payload or parameters validation failed.",
        details=sanitized_errors,
    )


async def unhandled_exception_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all handler for unhandled internal exceptions."""
    request_id = get_request_id()
    logger.error(
        "Unhandled server exception occurred",
        request_id=request_id,
        exc_info=exc,
    )

    message = (
        f"Internal server error: {exc}"
        if settings.DEBUG
        else "An unexpected internal server error occurred."
    )
    return _build_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message=message,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all centralized exception handlers on the FastAPI application."""
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
