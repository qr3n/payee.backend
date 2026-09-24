from typing import Any
from uuid import uuid4

import structlog
import taskiq_fastapi
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult
from taskiq.middlewares import SmartRetryMiddleware
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from app.core.config import settings
from app.core.logging import get_logger
from app.core.middleware import REQUEST_ID_CTX, get_request_id

logger = get_logger(__name__)


class TaskiqCorrelationMiddleware(TaskiqMiddleware):
    """
    Middleware that propagates Request ID (Correlation ID) from web requests
    into background tasks, ensuring end-to-end distributed tracing.
    """

    def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        """Inject current request ID into message labels before enqueuing."""
        req_id = get_request_id() or str(uuid4())
        message.labels["request_id"] = req_id
        return message

    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Extract request ID from labels and bind to execution context & structlog."""
        req_id = message.labels.get("request_id") or str(uuid4())
        REQUEST_ID_CTX.set(req_id)
        structlog.contextvars.bind_contextvars(
            request_id=req_id,
            task_name=message.task_name,
            task_id=message.task_id,
        )
        return message

    def post_execute(
        self,
        _message: TaskiqMessage,
        _result: TaskiqResult[object],
    ) -> None:
        """Clean up context variables after task completion."""
        structlog.contextvars.unbind_contextvars("request_id", "task_name", "task_id")

    def on_error(
        self,
        message: TaskiqMessage,
        _result: TaskiqResult[object],
        exception: BaseException,
    ) -> None:
        """Log structured error when a background task fails."""
        logger.error(
            "Background task failed with exception",
            task_name=message.task_name,
            task_id=message.task_id,
            exc_info=exception,
        )


# Setup Redis Result Backend for task result caching (TTL: 1 hour)
result_backend: RedisAsyncResultBackend[Any] = RedisAsyncResultBackend(
    redis_url=settings.redis_uri,
    keep_results=True,
    result_ex_time=3600,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
    socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
)

# Setup ListQueueBroker (FIFO task queue on Redis) with retry & tracing
# socket_timeout=None is required so BRPOP doesn't time out on idle queue
broker = (
    ListQueueBroker(
        url=settings.redis_uri,
        queue_name="default",
        socket_timeout=None,
        socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
        health_check_interval=30,
    )
    .with_result_backend(result_backend)
    .with_middlewares(
        TaskiqCorrelationMiddleware(),
        SmartRetryMiddleware(
            default_retry_count=3,
            default_delay=2.0,
            use_jitter=True,
            use_delay_exponent=True,
            max_delay_exponent=60.0,
        ),
    )
)

# Initialize FastAPI dependency injection support in tasks
taskiq_fastapi.init(broker, "app.main:app")
