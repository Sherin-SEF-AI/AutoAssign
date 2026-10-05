"""Loads planning inputs for a service date from a stored snapshot."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import DriverRecord, TripRecord, VehicleRecord
from app.core.errors import NotFound
from app.db.models import Snapshot
from app.db.queries import soc_map
from app.db.snapshots import latest_snapshot, load_snapshot_records


@dataclass(frozen=True, slots=True)
class PlanningInputs:
    service_date: date
    snapshot_id: uuid.UUID
    trips: list[TripRecord]
    drivers: list[DriverRecord]
    vehicles: list[VehicleRecord]
    soc_pct: dict[uuid.UUID, float]


async def load_inputs(
    session: AsyncSession,
    service_date: date,
    *,
    snapshot_id: uuid.UUID | None = None,
    use_soc: bool = False,
) -> PlanningInputs:
    if snapshot_id is None:
        snap = await latest_snapshot(session, service_date)
        if snap is None:
            raise NotFound(f"no snapshot for {service_date}; run ingest first", code="snapshot_not_found")
        snapshot_id = snap.snapshot_id
    else:
        found = await session.get(Snapshot, snapshot_id)
        if found is None:
            raise NotFound(f"snapshot {snapshot_id} not found", code="snapshot_not_found")
    trips, drivers, vehicles = await load_snapshot_records(session, snapshot_id)
    soc = {k: v.soc_pct for k, v in (await soc_map(session, service_date)).items()} if use_soc else {}
    return PlanningInputs(service_date, snapshot_id, trips, drivers, vehicles, soc)
