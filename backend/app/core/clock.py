"""Clock abstraction. Jobs read time from here so they can run on simulated time."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol

from redis.asyncio import Redis

SIM_STATE_KEY = "sim:state"


class Clock(Protocol):
    async def now(self) -> datetime: ...

    @property
    def simulated(self) -> bool: ...


class WallClock:
    simulated = False

    async def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A settable clock for tests and the replay harness."""

    simulated = True

    def __init__(self, at: datetime):
        self._at = at

    def set(self, at: datetime) -> None:
        self._at = at

    async def now(self) -> datetime:
        return self._at


class SimState:
    """Simulator clock state persisted in Redis.

    sim_time advances as anchor_sim + (wall - anchor_wall) * speed while running.
    """

    def __init__(
        self,
        *,
        service_date: str,
        running: bool,
        speed: float,
        anchor_sim: datetime,
        anchor_wall: datetime,
        disturbances: dict[str, bool] | None = None,
    ):
        self.service_date = service_date
        self.running = running
        self.speed = speed
        self.anchor_sim = anchor_sim
        self.anchor_wall = anchor_wall
        self.disturbances = disturbances or {}

    def sim_now(self, wall: datetime) -> datetime:
        if not self.running:
            return self.anchor_sim
        return self.anchor_sim + (wall - self.anchor_wall) * self.speed

    def to_json(self) -> str:
        return json.dumps(
            {
                "service_date": self.service_date,
                "running": self.running,
                "speed": self.speed,
                "anchor_sim": self.anchor_sim.isoformat(),
                "anchor_wall": self.anchor_wall.isoformat(),
                "disturbances": self.disturbances,
            }
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> SimState:
        data = json.loads(raw)
        return cls(
            service_date=data["service_date"],
            running=bool(data["running"]),
            speed=float(data["speed"]),
            anchor_sim=datetime.fromisoformat(data["anchor_sim"]),
            anchor_wall=datetime.fromisoformat(data["anchor_wall"]),
            disturbances=dict(data.get("disturbances") or {}),
        )


async def load_sim_state(redis: Redis) -> SimState | None:
    raw = await redis.get(SIM_STATE_KEY)
    if raw is None:
        return None
    return SimState.from_json(raw)


async def save_sim_state(redis: Redis, state: SimState) -> None:
    await redis.set(SIM_STATE_KEY, state.to_json())


class SimAwareClock:
    """Returns simulated time while a simulation state exists in Redis, wall time otherwise."""

    def __init__(self, redis: Redis):
        self._redis = redis
        self._last_simulated = False

    @property
    def simulated(self) -> bool:
        return self._last_simulated

    async def now(self) -> datetime:
        wall = datetime.now(UTC)
        state = await load_sim_state(self._redis)
        if state is None:
            self._last_simulated = False
            return wall
        self._last_simulated = True
        return state.sim_now(wall)
