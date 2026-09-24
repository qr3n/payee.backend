import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

logger = get_logger("app.access")

# Context variable for holding the current request ID across async tasks
REQUEST_ID_CTX: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Retrieve the current request ID from the execution context."""
    return REQUEST_ID_CTX.get()


class RequestIDMiddleware:
    """
    Pure ASGI middleware that extracts or generates a Request ID (Correlation ID).
    Propagates the ID via contextvars, structlog, request state, and HTTP headers,
    and logs structured HTTP access metrics (status, method, path, duration_ms).
    """

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self.header_name_bytes = header_name.lower().encode("latin1")
        self.raw_header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check for existing X-Request-ID header in incoming request
        incoming_id = ""
        for name, value in scope.get("headers", []):
            if name.lower() == self.header_name_bytes:
                incoming_id = value.decode("latin1").strip()
                break

        request_id = incoming_id if incoming_id else str(uuid.uuid4())

        # Set ContextVar and Starlette request state
        token = REQUEST_ID_CTX.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                headers = list(message.get("headers", []))
                headers.append((self.header_name_bytes, request_id.encode("latin1")))
                message["headers"] = headers
            await send(message)

        start_time = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            path = scope.get("path", "")
            # Silence noisy high-frequency monitoring endpoints in standard logs
            if path in ("/health", "/ready", "/metrics"):
                logger.debug(
                    "HTTP request completed",
                    method=scope.get("method"),
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
            else:
                logger.info(
                    "HTTP request completed",
                    method=scope.get("method"),
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
            structlog.contextvars.unbind_contextvars("request_id")
            REQUEST_ID_CTX.reset(token)
