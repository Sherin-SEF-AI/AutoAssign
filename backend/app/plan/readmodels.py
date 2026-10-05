"""Read models that join snapshots, live state and the current plan."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.queries import (
    current_plan,
    drivers_in_snapshot,
    plan_assignments,
    trip_live_map,
    trips_in_snapshot,
)
from app.db.snapshots import latest_snapshot


class TripRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: uuid.UUID
    service_date: date
    channel: str
    scheduled_pickup_at: datetime
    pickup_lat: float
    pickup_lng: float
    pickup_address: str
    drop_lat: float
    drop_lng: float
    drop_address: str
    trip_type: str
    package_hours: float | None
    pax: int
    luggage: int
    vehicle_class: str
    tags: dict[str, Any]
    account_id: str | None
    status: str
    actual_pickup_at: datetime | None
    actual_drop_at: datetime | None
    actual_distance_m: float | None
    driver_id: uuid.UUID | None = None
    driver_name: str | None = None
    planned_pickup_at: datetime | None = None
    planned_drop_at: datetime | None = None
    estimate_p50_s: int | None = None
    estimate_p80_s: int | None = None
    estimate_source: str | None = None
    unassigned_reason: str | None = None
    locked: bool = False


async def trip_rows_for_date(session: AsyncSession, service_date: date) -> list[TripRow]:
    snap = await latest_snapshot(session, service_date)
    if snap is None:
        return []
    trips = await trips_in_snapshot(session, snap.snapshot_id)
    live = await trip_live_map(session, service_date)
    names = {d.driver_id: d.name for d in await drivers_in_snapshot(session, snap.snapshot_id)}
    plan = await current_plan(session, service_date)
    assignments = {a.trip_id: a for a in await plan_assignments(session, plan.plan_id)} if plan else {}
    rows: list[TripRow] = []
    for t in trips:
        row = TripRow.model_validate(t)
        lv = live.get(t.trip_id)
        updates: dict[str, object] = {}
        if lv is not None:
            updates.update(
                status=lv.status,
                actual_pickup_at=lv.actual_pickup_at or t.actual_pickup_at,
                actual_drop_at=lv.actual_drop_at or t.actual_drop_at,
                actual_distance_m=lv.actual_distance_m or t.actual_distance_m,
            )
        a = assignments.get(t.trip_id)
        if a is not None:
            updates.update(
                driver_id=a.driver_id,
                driver_name=names.get(a.driver_id) if a.driver_id else None,
                planned_pickup_at=a.planned_pickup_at,
                planned_drop_at=a.planned_drop_at,
                estimate_p50_s=a.trip_p50_s,
                estimate_p80_s=a.trip_s,
                estimate_source=a.trip_source,
                unassigned_reason=a.unassigned_reason,
                locked=a.locked,
            )
        rows.append(row.model_copy(update=updates) if updates else row)
    return rows
