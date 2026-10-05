"""Circuit breaker with state in Redis so every process sees the same provider state."""

from __future__ import annotations

from typing import Literal

from redis.asyncio import Redis

State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(self, redis: Redis, provider: str, *, threshold: int, open_s: int):
        self.redis = redis
        self.provider = provider
        self.threshold = threshold
        self.open_s = open_s
        self._k_fail = f"breaker:{provider}:failures"
        self._k_open = f"breaker:{provider}:open"
        self._k_half = f"breaker:{provider}:half"

    async def state(self) -> State:
        if await self.redis.exists(self._k_open):
            return "open"
        if await self.redis.exists(self._k_half):
            return "half_open"
        return "closed"

    async def allow(self) -> bool:
        return not await self.redis.exists(self._k_open)

    async def record_success(self) -> None:
        await self.redis.delete(self._k_fail, self._k_half)

    async def record_failure(self) -> bool:
        """Returns True when this failure opened the circuit."""
        if await self.redis.exists(self._k_half):
            await self._open()
            return True
        failures = await self.redis.incr(self._k_fail)
        await self.redis.expire(self._k_fail, self.open_s * 6)
        if failures >= self.threshold:
            await self._open()
            return True
        return False

    async def _open(self) -> None:
        pipe = self.redis.pipeline()
        pipe.set(self._k_open, "1", ex=self.open_s)
        pipe.set(self._k_half, "1")
        pipe.delete(self._k_fail)
        await pipe.execute()

    async def reset(self) -> None:
        await self.redis.delete(self._k_fail, self._k_open, self._k_half)
