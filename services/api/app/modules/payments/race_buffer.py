"""
Redis event buffer for the payment race preflight pattern.

Provides a replay-capable event buffer using Redis List + Pub/Sub.
Events are persisted in a List (replay buffer) and real-time notifications
are broadcast via Pub/Sub for live SSE streaming.

Key layout (all keys expire after RACE_BUFFER_TTL_SEC):
    race:{batch_id}:events  — List of SSE-formatted event strings
    race:{batch_id}:status  — String: "running" | "done" | "timeout"
    race:{batch_id}          — Pub/Sub channel for new-event notifications
"""

import json
from uuid import UUID

from app.core.logging import get_logger
from app.core.redis import get_redis_client

logger = get_logger(__name__)

RACE_BUFFER_TTL_SEC = 300  # 5 minutes


def _events_key(batch_id: UUID) -> str:
    return f"race:{batch_id}:events"


def _status_key(batch_id: UUID) -> str:
    return f"race:{batch_id}:status"


def get_channel_name(batch_id: UUID) -> str:
    """Return the Pub/Sub channel name for the given batch."""
    return f"race:{batch_id}"


async def init_race_buffer(
    batch_id: UUID,
    scenarios: list[str],
) -> None:
    """
    Initialize race buffer in Redis.

    Creates a status key ("running"), pushes the initial ``started`` SSE event
    into the events list, and sets TTL on all keys.
    """
    redis = get_redis_client()
    status_k = _status_key(batch_id)
    events_k = _events_key(batch_id)

    started_data = json.dumps(
        {"batch_id": str(batch_id), "scenarios": scenarios},
        ensure_ascii=False,
    )
    started_event = f"event: started\ndata: {started_data}\n\n"

    pipe = redis.pipeline(transaction=False)
    pipe.set(status_k, "running", ex=RACE_BUFFER_TTL_SEC)
    pipe.rpush(events_k, started_event)
    pipe.expire(events_k, RACE_BUFFER_TTL_SEC)
    await pipe.execute()

    logger.info(
        "race_buffer_initialized",
        batch_id=str(batch_id),
        scenarios=scenarios,
    )


async def push_race_event(batch_id: UUID, sse_event: str) -> None:
    """Append an SSE event to the replay buffer and notify pub/sub subscribers."""
    redis = get_redis_client()
    events_k = _events_key(batch_id)
    channel = get_channel_name(batch_id)

    pipe = redis.pipeline(transaction=False)
    pipe.rpush(events_k, sse_event)
    pipe.expire(events_k, RACE_BUFFER_TTL_SEC)
    pipe.publish(channel, "new_event")
    await pipe.execute()


async def finish_race(
    batch_id: UUID,
    *,
    status: str,
    total: int,
    succeeded: int,
    failed: int,
    duration_sec: float,
) -> None:
    """
    Mark race as finished: set terminal status, push ``done`` SSE event,
    and publish ``done`` notification so SSE subscribers can close.
    """
    redis = get_redis_client()

    done_payload = json.dumps(
        {
            "batch_id": str(batch_id),
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "duration_sec": duration_sec,
        },
        ensure_ascii=False,
    )
    done_event = f"event: done\ndata: {done_payload}\n\n"

    pipe = redis.pipeline(transaction=False)
    pipe.set(_status_key(batch_id), status, ex=RACE_BUFFER_TTL_SEC)
    pipe.rpush(_events_key(batch_id), done_event)
    pipe.expire(_events_key(batch_id), RACE_BUFFER_TTL_SEC)
    pipe.publish(get_channel_name(batch_id), "done")
    await pipe.execute()

    logger.info(
        "race_buffer_finished",
        batch_id=str(batch_id),
        status=status,
        succeeded=succeeded,
        failed=failed,
        duration_sec=duration_sec,
    )


async def get_race_status(batch_id: UUID) -> str | None:
    """Return current race status or ``None`` if batch does not exist / expired."""
    redis = get_redis_client()
    result = await redis.get(_status_key(batch_id))
    if result is None:
        return None
    return result.decode() if isinstance(result, bytes) else str(result)


async def get_buffered_events(batch_id: UUID, start: int = 0) -> list[str]:
    """Read SSE events from the replay buffer starting at *start* index."""
    redis = get_redis_client()
    raw_events = await redis.lrange(_events_key(batch_id), start, -1)
    return [e.decode() if isinstance(e, bytes) else str(e) for e in raw_events]
