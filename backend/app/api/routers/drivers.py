"""Roster for a date and a driver's published sequence."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import Session, User
from app.api.schemas import DriverOut
from app.core.errors import NotFound
from app.db.queries import current_plan as current_plan_q
from app.db.queries import (
    drivers_in_snapshot,
    plan_assignments,
    published_plan,
    soc_map,
    trips_in_snapshot,
    vehicles_in_snapshot,
)
from app.db.snapshots import latest_snapshot

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("", response_model=list[DriverOut])
async def list_drivers(
    session: Session, _: User, date_: Annotated[date, Query(alias="date")]
) -> list[DriverOut]:
    snap = await latest_snapshot(session, date_)
    if snap is None:
        return []
    drivers = await drivers_in_snapshot(session, snap.snapshot_id)
    vehicles = {v.vehicle_id: v for v in await vehicles_in_snapshot(session, snap.snapshot_id)}
    socs = await soc_map(session, date_)
    plan = await current_plan_q(session, date_)
    counts: dict[uuid.UUID, int] = {}
    if plan is not None:
        for a in await plan_assignments(session, plan.plan_id):
            if a.driver_id is not None:
                counts[a.driver_id] = counts.get(a.driver_id, 0) + 1
    out: list[DriverOut] = []
    for d in drivers:
        row = DriverOut.model_validate(d)
        v = vehicles.get(d.vehicle_id) if d.vehicle_id else None
        soc = socs.get(d.vehicle_id) if d.vehicle_id else None
        out.append(
            row.model_copy(
                update={
                    "vehicle_model": v.variant if v else None,
                    "vehicle_registration": v.registration if v else None,
                    "vehicle_status": v.status if v else None,
                    "soc_pct": soc.soc_pct if soc else None,
                    "odometer_km": soc.odometer_km if soc else None,
                    "soc_reported_at": soc.reported_at if soc else None,
                    "assigned_trips": counts.get(d.driver_id, 0),
                }
            )
        )
    out.sort(key=lambda r: (r.shift_start_at.timestamp() if r.shift_start_at else float("inf"), r.name))
    return out


class ScheduleStop(BaseModel):
    seq: int
    trip_id: uuid.UUID
    planned_depart_at: datetime | None
    planned_arrive_pickup_at: datetime | None
    planned_pickup_at: datetime | None
    planned_drop_at: datetime | None
    pickup_address: str
    drop_address: str
    trip_type: str
    locked: bool


class ScheduleOut(BaseModel):
    driver_id: uuid.UUID
    service_date: date
    plan_id: uuid.UUID | None
    plan_version: int | None
    published: bool
    stops: list[ScheduleStop]


@router.get("/{driver_id}/schedule", response_model=ScheduleOut)
async def driver_schedule(
    driver_id: uuid.UUID, session: Session, _: User, date_: Annotated[date, Query(alias="date")]
) -> ScheduleOut:
    snap = await latest_snapshot(session, date_)
    if snap is None or driver_id not in {
        d.driver_id for d in await drivers_in_snapshot(session, snap.snapshot_id)
    }:
        raise NotFound(f"driver {driver_id} not on the roster for {date_}", code="driver_not_found")
    plan = await published_plan(session, date_)
    if plan is None:
        return ScheduleOut(
            driver_id=driver_id,
            service_date=date_,
            plan_id=None,
            plan_version=None,
            published=False,
            stops=[],
        )
    trips = {t.trip_id: t for t in await trips_in_snapshot(session, plan.snapshot_id)}
    stops: list[ScheduleStop] = []
    for a in await plan_assignments(session, plan.plan_id):
        if a.driver_id != driver_id or a.seq is None:
            continue
        t = trips.get(a.trip_id)
        stops.append(
            ScheduleStop(
                seq=a.seq,
                trip_id=a.trip_id,
                planned_depart_at=a.planned_depart_at,
                planned_arrive_pickup_at=a.planned_arrive_pickup_at,
                planned_pickup_at=a.planned_pickup_at,
                planned_drop_at=a.planned_drop_at,
                pickup_address=t.pickup_address if t else "",
                drop_address=t.drop_address if t else "",
                trip_type=t.trip_type if t else "",
                locked=a.locked,
            )
        )
    stops.sort(key=lambda s: s.seq)
    return ScheduleOut(
        driver_id=driver_id,
        service_date=date_,
        plan_id=plan.plan_id,
        plan_version=plan.version,
        published=True,
        stops=stops,
    )
