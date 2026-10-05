"""Redis based job locks (SET NX with TTL) released only by their owner."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class LockBusy(Exception):
    pass


@asynccontextmanager
async def redis_lock(redis: Redis, name: str, ttl_s: int) -> AsyncIterator[str]:
    key = f"lock:job:{name}"
    token = uuid.uuid4().hex
    acquired = await redis.set(key, token, nx=True, ex=ttl_s)
    if not acquired:
        raise LockBusy(name)
    try:
        yield token
    finally:
        await redis.eval(_RELEASE, 1, key, token)
