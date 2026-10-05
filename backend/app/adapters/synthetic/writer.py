"""Writes the synthetic world into snapshot tables through the shared snapshot writer."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.adapters.synthetic.generate import SyntheticWorld
from app.config import Settings
from app.db.session import SessionFactory, session_scope
from app.db.snapshots import upsert_hubs, upsert_soc_checkins, write_snapshot

Progress = Callable[[int, int, date], Awaitable[None]]


@dataclass(slots=True)
class GenerateResult:
    seed: int
    days: list[str] = field(default_factory=list)
    trips: int = 0
    snapshots_created: int = 0


async def generate_dataset(
    factory: SessionFactory,
    settings: Settings,
    *,
    seed: int,
    today: date,
    days_past: int,
    days_future: int,
    progress: Progress | None = None,
) -> GenerateResult:
    """Write past days (with actuals) and future days (bookings) as snapshots."""
    world = SyntheticWorld(seed)
    result = GenerateResult(seed=seed)
    dates = [today + timedelta(days=offset) for offset in range(-days_past, days_future + 1)]
    async with session_scope(factory) as session:
        await upsert_hubs(session, world.hubs)
    for i, d in enumerate(dates):
        async with session_scope(factory) as session:
            trips = world.trips(d, today)
            write = await write_snapshot(
                session,
                settings,
                service_date=d,
                source="synthetic",
                trips=trips,
                drivers=world.drivers(d),
                vehicles=world.vehicles(d),
                reason="synthetic_generate",
            )
            if d < today:
                await upsert_soc_checkins(session, world.soc_checkins(d))
        result.days.append(d.isoformat())
        result.trips += len(trips)
        result.snapshots_created += int(write.created)
        if progress is not None:
            await progress(i + 1, len(dates), d)
    return result
