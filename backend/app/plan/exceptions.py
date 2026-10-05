"""Fleet exceptions: ops edits to vehicle status and driver shifts for a service date."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import DriverRecord, VehicleRecord
from app.db.models import FleetException

KINDS = ("vehicle_status", "driver_shift")


async def latest_exceptions(
    session: AsyncSession, service_date: date
) -> dict[tuple[str, uuid.UUID], dict[str, Any]]:
    rows = (
        await session.execute(
            select(FleetException)
            .where(FleetException.service_date == service_date)
            .order_by(FleetException.created_at)
        )
    ).scalars()
    latest: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}
    for r in rows:
        latest[(r.kind, r.entity_id)] = dict(r.payload)
    return latest


async def apply_fleet_exceptions(
    session: AsyncSession,
    service_date: date,
    drivers: Sequence[DriverRecord],
    vehicles: Sequence[VehicleRecord],
) -> tuple[list[DriverRecord], list[VehicleRecord]]:
    ex = await latest_exceptions(session, service_date)
    out_v: list[VehicleRecord] = []
    for v in vehicles:
        payload = ex.get(("vehicle_status", v.vehicle_id))
        out_v.append(v.model_copy(update={"status": payload["status"]}) if payload else v)
    out_d: list[DriverRecord] = []
    for d in drivers:
        payload = ex.get(("driver_shift", d.driver_id))
        if payload:
            update: dict[str, Any] = {}
            if "active" in payload:
                update["active"] = bool(payload["active"])
            for key in ("shift_start_at", "shift_end_at"):
                if payload.get(key):
                    update[key] = datetime.fromisoformat(payload[key])
            if "channel_today" in payload:
                update["channel_today"] = payload["channel_today"]
            out_d.append(d.model_copy(update=update))
        else:
            out_d.append(d)
    return out_d, out_v
