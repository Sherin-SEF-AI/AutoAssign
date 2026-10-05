"""Event bus over Redis pub/sub, consumed by the SSE endpoint."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

CHANNEL = "sse:events"
EVENT_TYPES = (
    "plan.drafted",
    "plan.published",
    "plan.repaired",
    "plan.updated",
    "monitor.tick",
    "sim.clock",
    "budget.warning",
    "job.finished",
    "data.regenerated",
)


def encode_event(event: str, data: dict[str, Any]) -> str:
    return json.dumps(
        {"event": event, "data": data, "emitted_at": datetime.now(UTC).isoformat()}, default=str
    )


async def publish(redis: Redis, event: str, data: dict[str, Any]) -> None:
    if event not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event}")
    await redis.publish(CHANNEL, encode_event(event, data))


async def subscribe(redis: Redis) -> AsyncIterator[dict[str, Any]]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode()
            yield json.loads(raw)
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
