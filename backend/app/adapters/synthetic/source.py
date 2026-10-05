"""SyntheticSource: the default DataSource, backed by the seeded synthetic world."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import date, datetime
from typing import Any

from redis.asyncio import Redis

from app.adapters.base import (
    DriverRecord,
    HubRecord,
    PingRecord,
    SocCheckinRecord,
    TripEvent,
    TripRecord,
    VehicleRecord,
)
from app.adapters.synthetic.generate import SyntheticWorld
from app.adapters.synthetic.writer import Progress, generate_dataset
from app.db.maintenance import reset_dataset
from app.db.session import SessionFactory, session_scope

SEED_KEY = "synthetic:seed"


class SyntheticSource:
    name = "synthetic"

    def __init__(self, seed: int, today: Callable[[], date], redis: Redis | None = None):
        self._default_seed = seed
        self._today = today
        self._redis = redis
        self._worlds: dict[int, SyntheticWorld] = {}

    async def _world(self) -> SyntheticWorld:
        seed = self._default_seed
        if self._redis is not None:
            raw = await self._redis.get(SEED_KEY)
            if raw is not None:
                seed = int(raw)
        if seed not in self._worlds:
            self._worlds[seed] = SyntheticWorld(seed)
        return self._worlds[seed]

    async def list_trips(self, service_date: date) -> list[TripRecord]:
        return (await self._world()).trips(service_date, self._today())

    async def list_drivers(self, service_date: date) -> list[DriverRecord]:
        return (await self._world()).drivers(service_date)

    async def list_vehicles(self) -> list[VehicleRecord]:
        return (await self._world()).vehicles(self._today())

    async def list_hubs(self) -> list[HubRecord]:
        return list((await self._world()).hubs)

    async def list_soc_checkins(self, service_date: date) -> list[SocCheckinRecord]:
        return (await self._world()).soc_checkins(service_date)

    async def stream_pings(self, since: datetime) -> AsyncIterator[PingRecord]:
        # Pings come only from the day simulator, which pushes them like the driver app would.
        empty: tuple[PingRecord, ...] = ()
        for item in empty:
            yield item

    async def trip_events(self, since: datetime) -> AsyncIterator[TripEvent]:
        # Trip events are pushed by the simulator through the same path as POST /events/trip.
        empty: tuple[TripEvent, ...] = ()
        for item in empty:
            yield item

    async def regenerate(
        self,
        factory: SessionFactory,
        settings: Any,
        *,
        seed: int,
        days_past: int,
        days_future: int,
        today: date,
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        async with session_scope(factory) as session:
            await reset_dataset(session)
        if self._redis is not None:
            await self._redis.set(SEED_KEY, str(seed))
        result = await generate_dataset(
            factory,
            settings,
            seed=seed,
            today=today,
            days_past=days_past,
            days_future=days_future,
            progress=progress,
        )
        return {
            "seed": result.seed,
            "days": len(result.days),
            "first_day": result.days[0] if result.days else None,
            "last_day": result.days[-1] if result.days else None,
            "trips": result.trips,
            "snapshots_created": result.snapshots_created,
        }

    async def aclose(self) -> None:
        return None
