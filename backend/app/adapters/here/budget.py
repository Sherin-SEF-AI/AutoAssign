"""Daily provider request budgets in Redis, reset at midnight IST."""

from __future__ import annotations

from datetime import datetime, timedelta

from redis.asyncio import Redis

from app.core.events import publish
from app.core.logging import get_logger
from app.core.metrics import BUDGET_REMAINING
from app.core.timeutil import IST, to_ist

log = get_logger("budget")

PROVIDERS = ("here_routing", "here_matrix", "here_matrix_elements")

# Atomically consume amounts from several counters only if every one stays within its cap.
_CONSUME = """
local n = #KEYS
for i = 1, n do
  local current = tonumber(redis.call('get', KEYS[i]) or '0')
  local amount = tonumber(ARGV[i])
  local cap = tonumber(ARGV[n + i])
  if current + amount > cap then
    return i
  end
end
for i = 1, n do
  redis.call('incrby', KEYS[i], tonumber(ARGV[i]))
  redis.call('expireat', KEYS[i], tonumber(ARGV[2 * n + 1]))
end
return 0
"""


def day_tag(now: datetime) -> str:
    return to_ist(now).strftime("%Y%m%d")


def budget_key(provider: str, now: datetime) -> str:
    return f"budget:{provider}:{day_tag(now)}"


def _expire_at(now: datetime) -> int:
    local = to_ist(now)
    midnight = datetime(local.year, local.month, local.day, tzinfo=IST) + timedelta(days=1)
    # Keep the counter one hour past midnight IST so the day's figure can still be read.
    return int((midnight + timedelta(hours=1)).timestamp())


class BudgetGuard:
    def __init__(self, redis: Redis, caps: dict[str, int]):
        self.redis = redis
        self.caps = caps

    async def try_consume(self, amounts: dict[str, int], now: datetime) -> bool:
        providers = list(amounts)
        keys = [budget_key(p, now) for p in providers]
        args = [str(amounts[p]) for p in providers] + [str(self.caps[p]) for p in providers]
        args.append(str(_expire_at(now)))
        rejected = int(await self.redis.eval(_CONSUME, len(keys), *keys, *args))
        if rejected:
            provider = providers[rejected - 1]
            await self._warn_once(provider, now)
            return False
        for p in providers:
            BUDGET_REMAINING.labels(provider=p).set(max(0, self.caps[p] - await self.used(p, now)))
        return True

    async def used(self, provider: str, now: datetime) -> int:
        raw = await self.redis.get(budget_key(provider, now))
        return int(raw) if raw is not None else 0

    async def exhausted(self, provider: str, now: datetime) -> bool:
        return await self.used(provider, now) >= self.caps[provider]

    async def snapshot(self, now: datetime) -> dict[str, dict[str, int]]:
        out = {}
        for p in PROVIDERS:
            used = await self.used(p, now)
            cap = self.caps[p]
            BUDGET_REMAINING.labels(provider=p).set(max(0, cap - used))
            out[p] = {"used": used, "cap": cap, "remaining": max(0, cap - used)}
        return out

    async def _warn_once(self, provider: str, now: datetime) -> None:
        flag = f"budget:{provider}:{day_tag(now)}:warned"
        if await self.redis.set(flag, "1", nx=True, ex=_expire_at(now) - int(now.timestamp())):
            log.warning("provider_budget_exhausted", provider=provider, cap=self.caps[provider])
            BUDGET_REMAINING.labels(provider=provider).set(0)
            await publish(self.redis, "budget.warning", {"provider": provider, "cap": self.caps[provider]})


def caps_from_settings(routing: int, matrix: int, elements: int) -> dict[str, int]:
    return {"here_routing": routing, "here_matrix": matrix, "here_matrix_elements": elements}
