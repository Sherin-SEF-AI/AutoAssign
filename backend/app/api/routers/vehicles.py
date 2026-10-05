"""Fleet and the morning SOC and odometer entry."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.adapters.base import SocCheckinRecord
from app.api.deps import Ctx, Session, User
from app.api.schemas import HubOut, VehicleOut
from app.core.errors import NotFound
from app.core.timeutil import service_date_of, utcnow
from app.db.queries import (
    drivers_in_snapshot,
    latest_vehicle_snapshot_id,
    list_hubs,
    soc_map,
    vehicles_in_snapshot,
)
from app.db.snapshots import latest_snapshot, upsert_soc_checkins

router = APIRouter(tags=["vehicles"])


@router.get("/vehicles", response_model=list[VehicleOut])
async def list_vehicles(
    session: Session, ctx: Ctx, _: User, date_: Annotated[date | None, Query(alias="date")] = None
) -> list[VehicleOut]:
    day = date_ or await ctx.today()
    snap = await latest_snapshot(session, day)
    snapshot_id = snap.snapshot_id if snap else await latest_vehicle_snapshot_id(session)
    if snapshot_id is None:
        return []
    hubs = {h.hub_id: h.name for h in await list_hubs(session)}
    drivers = {d.vehicle_id: d for d in await drivers_in_snapshot(session, snapshot_id) if d.vehicle_id}
    socs = await soc_map(session, day)
    out = []
    for v in await vehicles_in_snapshot(session, snapshot_id):
        d = drivers.get(v.vehicle_id)
        soc = socs.get(v.vehicle_id)
        out.append(
            VehicleOut.model_validate(v).model_copy(
                update={
                    "hub_name": hubs.get(v.hub_id) if v.hub_id else None,
                    "driver_id": d.driver_id if d else None,
                    "driver_name": d.name if d else None,
                    "soc_pct": soc.soc_pct if soc else None,
                }
            )
        )
    out.sort(key=lambda v: (v.model, v.registration))
    return out


@router.get("/hubs", response_model=list[HubOut])
async def hubs(session: Session, _: User) -> list[HubOut]:
    return [HubOut.model_validate(h) for h in await list_hubs(session)]


class SocCheckinIn(BaseModel):
    soc_pct: float = Field(ge=0, le=100)
    odometer_km: float | None = Field(default=None, ge=0)
    service_date: date | None = None
    driver_id: uuid.UUID | None = None


class SocCheckinOut(BaseModel):
    vehicle_id: uuid.UUID
    service_date: date
    soc_pct: float
    odometer_km: float | None


@router.post("/vehicles/{vehicle_id}/soc-checkin", response_model=SocCheckinOut, status_code=201)
async def soc_checkin(
    vehicle_id: uuid.UUID, body: SocCheckinIn, session: Session, ctx: Ctx, _: User
) -> SocCheckinOut:
    now = await ctx.clock.now()
    day = body.service_date or service_date_of(now)
    snap = await latest_snapshot(session, day)
    snapshot_id = snap.snapshot_id if snap else await latest_vehicle_snapshot_id(session)
    known = {v.vehicle_id for v in await vehicles_in_snapshot(session, snapshot_id)} if snapshot_id else set()
    if vehicle_id not in known:
        raise NotFound(f"vehicle {vehicle_id} not found", code="vehicle_not_found")
    driver_id = body.driver_id
    if driver_id is None and snapshot_id is not None:
        driver_id = next(
            (
                d.driver_id
                for d in await drivers_in_snapshot(session, snapshot_id)
                if d.vehicle_id == vehicle_id
            ),
            None,
        )
    record = SocCheckinRecord(
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        service_date=day,
        soc_pct=body.soc_pct,
        odometer_km=body.odometer_km,
        reported_at=now if now.tzinfo else utcnow(),
    )
    await upsert_soc_checkins(session, [record], source="ops")
    return SocCheckinOut(
        vehicle_id=vehicle_id, service_date=day, soc_pct=body.soc_pct, odometer_km=body.odometer_km
    )
