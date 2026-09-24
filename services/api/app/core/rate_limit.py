import time

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.api.deps import get_redis
from app.core.exceptions import RateLimitException


class RateLimiter:
    """
    Sliding-window rate limiter using Redis sorted sets (ZSET).
    Enforces request limits per client IP over a rolling time window.
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
        key_prefix: str = "rate_limit",
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    async def __call__(
        self,
        request: Request,
        redis: Redis = Depends(get_redis),
    ) -> None:
        # Extract client IP from proxy headers or direct client connection
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        else:
            client_ip = "unknown"

        route_key = request.scope.get("path", "unknown")
        redis_key = f"{self.key_prefix}:{route_key}:{client_ip}"

        now = time.time()
        clear_before = now - self.window_seconds

        try:
            # Atomic sliding-window pipeline
            pipe = redis.pipeline()
            pipe.zremrangebyscore(redis_key, 0, clear_before)
            pipe.zcard(redis_key)
            pipe.zadd(redis_key, {str(now): now})
            pipe.expire(redis_key, self.window_seconds + 1)
            results = await pipe.execute()

            current_requests = results[1]
            if current_requests >= self.max_requests:
                raise RateLimitException(
                    message=(
                        f"Rate limit of {self.max_requests} requests per "
                        f"{self.window_seconds}s exceeded."
                    ),
                    code="RATE_LIMIT_EXCEEDED",
                    details={
                        "max_requests": self.max_requests,
                        "window_seconds": self.window_seconds,
                    },
                )
        except RateLimitException:
            raise
        except Exception:
            # Fail-open if Redis encounters connection issues to avoid blocking users
            return
