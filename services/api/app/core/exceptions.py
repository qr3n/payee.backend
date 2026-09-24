from typing import Any


class AppException(Exception):
    """
    Base application exception.
    Raise this exception or its subclasses from business logic or service layers.
    """

    def __init__(
        self,
        message: str,
        code: str = "BAD_REQUEST",
        status_code: int = 400,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


class NotFoundException(AppException):
    """Raised when a requested resource does not exist."""

    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "NOT_FOUND",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=404, details=details)


class BadRequestException(AppException):
    """Raised when client input or state violates business preconditions."""

    def __init__(
        self,
        message: str = "Bad request",
        code: str = "BAD_REQUEST",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=400, details=details)


class ConflictException(AppException):
    """Raised when a resource state conflicts with the requested action."""

    def __init__(
        self,
        message: str = "Resource conflict",
        code: str = "CONFLICT",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=409, details=details)


class UnauthorizedException(AppException):
    """Raised when authentication credentials are missing or invalid."""

    def __init__(
        self,
        message: str = "Authentication required",
        code: str = "UNAUTHORIZED",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=401, details=details)


class ForbiddenException(AppException):
    """Raised when authenticated user lacks permissions for an operation."""

    def __init__(
        self,
        message: str = "Access forbidden",
        code: str = "FORBIDDEN",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=403, details=details)


class RateLimitException(AppException):
    """Raised when client exceeds allowed request rate."""

    def __init__(
        self,
        message: str = "Too many requests. Please try again later.",
        code: str = "RATE_LIMIT_EXCEEDED",
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, code=code, status_code=429, details=details)
