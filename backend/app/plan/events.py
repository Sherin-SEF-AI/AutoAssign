"""Applies trip events and driver pings, the push path shared by the API and the simulator."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import PingRecord, TripEvent
from app.config import Settings
from app.core.errors import NotFound, Unprocessable
from app.db.models import InboundEvent, Ping, TripLive
from app.db.snapshots import latest_snapshot, load_snapshot_records, write_snapshot

PLANNING_EVENTS = ("trip.created", "trip.updated", "trip.cancelled")


@dataclass(frozen=True, slots=True)
class EventResult:
    event_id: str
    event_type: str
    trip_id: uuid.UUID
    service_date: str
    duplicate: bool
    snapshot_id: uuid.UUID | None = None
    replan: bool = False


async def apply_trip_event(session: AsyncSession, settings: Settings, event: TripEvent) -> EventResult:
    trip_id = event.resolved_trip_id()
    day = event.resolved_service_date()
    stmt = (
        insert(InboundEvent)
        .values(
            event_id=event.event_id,
            event_type=event.event_type,
            trip_id=trip_id,
            service_date=day,
            payload=event.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=[InboundEvent.event_id])
        .returning(InboundEvent.event_id)
    )
    if (await session.execute(stmt)).scalar_one_or_none() is None:
        return EventResult(event.event_id, event.event_type, trip_id, day.isoformat(), True)

    if event.event_type == "trip.status":
        if event.status is None:
            raise Unprocessable("trip.status needs a status", code="invalid_event")
        existing = await session.get(TripLive, trip_id)
        lifecycle = dict(existing.lifecycle) if existing else {}
        lifecycle[event.status] = event.occurred_at.isoformat()
        values = {
            "trip_id": trip_id,
            "service_date": day,
            "status": event.status,
            "driver_id": event.driver_id or (existing.driver_id if existing else None),
            "lifecycle": lifecycle,
            "actual_pickup_at": event.actual_pickup_at or (existing.actual_pickup_at if existing else None),
            "actual_drop_at": event.actual_drop_at or (existing.actual_drop_at if existing else None),
            "actual_distance_m": event.actual_distance_m
            or (existing.actual_distance_m if existing else None),
            "updated_at": event.occurred_at,
        }
        up = insert(TripLive).values(**values)
        up = up.on_conflict_do_update(
            index_elements=[TripLive.trip_id], set_={k: up.excluded[k] for k in values if k != "trip_id"}
        )
        await session.execute(up)
        return EventResult(event.event_id, event.event_type, trip_id, day.isoformat(), False)

    snap = await latest_snapshot(session, day)
    if snap is None:
        raise NotFound(f"no snapshot for {day}; the event cannot be applied", code="snapshot_not_found")
    trips, drivers, vehicles = await load_snapshot_records(session, snap.snapshot_id)
    by_id = {t.trip_id: t for t in trips}
    if event.event_type in ("trip.created", "trip.updated"):
        if event.trip is None:
            raise Unprocessable(f"{event.event_type} needs the trip record", code="invalid_event")
        if event.event_type == "trip.updated" and trip_id not in by_id:
            raise NotFound(f"trip {trip_id} is not in the snapshot for {day}", code="trip_not_found")
        by_id[trip_id] = event.trip
    else:
        current = by_id.get(trip_id)
        if current is None:
            raise NotFound(f"trip {trip_id} is not in the snapshot for {day}", code="trip_not_found")
        lifecycle = dict(current.lifecycle)
        lifecycle["cancelled"] = event.occurred_at
        by_id[trip_id] = current.model_copy(update={"status": "cancelled", "lifecycle": lifecycle})
    write = await write_snapshot(
        session,
        settings,
        service_date=day,
        source="event",
        trips=sorted(by_id.values(), key=lambda t: (t.scheduled_pickup_at, str(t.trip_id))),
        drivers=drivers,
        vehicles=vehicles,
        reason=event.event_type,
        parent_snapshot_id=snap.snapshot_id,
    )
    return EventResult(
        event.event_id,
        event.event_type,
        trip_id,
        day.isoformat(),
        False,
        write.snapshot_id,
        replan=write.created,
    )


async def insert_pings(session: AsyncSession, pings: Sequence[PingRecord]) -> int:
    if not pings:
        return 0
    rows = [p.model_dump() for p in pings]
    total = 0
    for i in range(0, len(rows), 1000):
        stmt = insert(Ping).values(rows[i : i + 1000]).on_conflict_do_nothing()
        result = await session.execute(stmt)
        total += result.rowcount or 0  # type: ignore[attr-defined]
    return total


async def latest_pings(session: AsyncSession, until: datetime, since: datetime) -> dict[uuid.UUID, Ping]:
    stmt = (
        select(Ping)
        .where(Ping.ts <= until, Ping.ts >= since)
        .order_by(Ping.driver_id, Ping.ts.desc())
        .distinct(Ping.driver_id)
    )
    return {p.driver_id: p for p in (await session.execute(stmt)).scalars()}


async def drivers_with_pings(session: AsyncSession, since: datetime, until: datetime) -> set[uuid.UUID]:
    stmt = select(Ping.driver_id).where(Ping.ts >= since, Ping.ts <= until).distinct()
    return set((await session.execute(stmt)).scalars())


def utc_now() -> datetime:
    return datetime.now(UTC)
