"""Snapshot writer and reader.

Whatever a DataSource returns lands here with a new snapshot_id. Identical content for a
service date reuses the latest snapshot so downstream jobs stay idempotent.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import DriverRecord, HubRecord, SocCheckinRecord, TripRecord, VehicleRecord
from app.config import Settings
from app.core.errors import Unprocessable
from app.db.models import DriverSnapshot, Hub, Snapshot, SocCheckin, TripSnapshot, VehicleSnapshot


@dataclass(frozen=True, slots=True)
class SnapshotWrite:
    snapshot_id: uuid.UUID
    created: bool
    counts: dict[str, int]


def content_hash(
    trips: Sequence[TripRecord], drivers: Sequence[DriverRecord], vehicles: Sequence[VehicleRecord]
) -> str:
    payload = {
        "trips": sorted((t.model_dump(mode="json") for t in trips), key=lambda r: r["trip_id"]),
        "drivers": sorted((d.model_dump(mode="json") for d in drivers), key=lambda r: r["driver_id"]),
        "vehicles": sorted((v.model_dump(mode="json") for v in vehicles), key=lambda r: r["vehicle_id"]),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def latest_snapshot(session: AsyncSession, service_date: date) -> Snapshot | None:
    stmt = (
        select(Snapshot)
        .where(Snapshot.service_date == service_date)
        .order_by(Snapshot.created_at.desc(), Snapshot.snapshot_id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def write_snapshot(
    session: AsyncSession,
    settings: Settings,
    *,
    service_date: date,
    source: str,
    trips: Sequence[TripRecord],
    drivers: Sequence[DriverRecord],
    vehicles: Sequence[VehicleRecord],
    reason: str = "ingest",
    parent_snapshot_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> SnapshotWrite:
    for t in trips:
        if t.service_date != service_date:
            raise Unprocessable(f"trip {t.trip_id} belongs to {t.service_date}, not {service_date}")
    for d in drivers:
        if d.service_date != service_date:
            raise Unprocessable(f"driver {d.driver_id} roster is for {d.service_date}, not {service_date}")
    unknown = sorted({v.model for v in vehicles} - set(settings.range_caps_json))
    if unknown:
        raise Unprocessable(f"vehicle models without a configured range cap: {', '.join(unknown)}")
    digest = content_hash(trips, drivers, vehicles)
    counts = {"trips": len(trips), "drivers": len(drivers), "vehicles": len(vehicles)}
    prev = await latest_snapshot(session, service_date)
    if prev is not None and prev.content_hash == digest:
        return SnapshotWrite(prev.snapshot_id, False, counts)
    stamp = now or datetime.now(UTC)
    snap = Snapshot(
        snapshot_id=uuid.uuid4(),
        service_date=service_date,
        source=source,
        content_hash=digest,
        parent_snapshot_id=parent_snapshot_id,
        reason=reason,
        counts=counts,
        created_at=stamp,
    )
    session.add(snap)
    await session.flush()
    if trips:
        await session.execute(
            insert(TripSnapshot),
            [_trip_row(t, snap.snapshot_id, stamp) for t in trips],
        )
    if drivers:
        await session.execute(
            insert(DriverSnapshot),
            [{"id": uuid.uuid4(), "snapshot_id": snap.snapshot_id, **d.model_dump()} for d in drivers],
        )
    if vehicles:
        await session.execute(
            insert(VehicleSnapshot),
            [
                {
                    "id": uuid.uuid4(),
                    "snapshot_id": snap.snapshot_id,
                    **v.model_dump(),
                    "planning_cap_km": settings.range_caps_json[v.model],
                    "e_roll_wh_per_km": settings.e_roll_json[v.model],
                }
                for v in vehicles
            ],
        )
    return SnapshotWrite(snap.snapshot_id, True, counts)


def _trip_row(t: TripRecord, snapshot_id: uuid.UUID, stamp: datetime) -> dict[str, Any]:
    data = t.model_dump()
    data["lifecycle"] = {k: v.isoformat() for k, v in t.lifecycle.items()}
    return {"id": uuid.uuid4(), "snapshot_id": snapshot_id, "snapshot_at": stamp, **data}


async def upsert_hubs(session: AsyncSession, hubs: Sequence[HubRecord]) -> int:
    for h in hubs:
        stmt = insert(Hub).values(**h.model_dump(), updated_at=datetime.now(UTC))
        stmt = stmt.on_conflict_do_update(
            index_elements=[Hub.hub_id],
            set_={
                k: stmt.excluded[k]
                for k in ("name", "lat", "lng", "ac_kw", "dc_kw", "ac_points", "dc_points")
            },
        )
        await session.execute(stmt)
    return len(hubs)


async def upsert_soc_checkins(
    session: AsyncSession, checkins: Sequence[SocCheckinRecord], *, source: str = "datasource"
) -> int:
    for c in checkins:
        stmt = insert(SocCheckin).values(id=uuid.uuid4(), source=source, **c.model_dump())
        # A check-in entered by the driver or ops wins over a later DataSource pull.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_soc_vehicle_date",
            set_={
                "soc_pct": stmt.excluded.soc_pct,
                "odometer_km": stmt.excluded.odometer_km,
                "reported_at": stmt.excluded.reported_at,
                "driver_id": stmt.excluded.driver_id,
                "source": stmt.excluded.source,
            },
            where=(SocCheckin.source == "datasource") | (stmt.excluded.source != "datasource"),
        )
        await session.execute(stmt)
    return len(checkins)


async def load_snapshot_records(
    session: AsyncSession, snapshot_id: uuid.UUID
) -> tuple[list[TripRecord], list[DriverRecord], list[VehicleRecord]]:
    """Rebuild canonical records from a stored snapshot."""
    trips = (
        await session.execute(
            select(TripSnapshot)
            .where(TripSnapshot.snapshot_id == snapshot_id)
            .order_by(TripSnapshot.scheduled_pickup_at, TripSnapshot.trip_id)
        )
    ).scalars()
    drivers = (
        await session.execute(
            select(DriverSnapshot)
            .where(DriverSnapshot.snapshot_id == snapshot_id)
            .order_by(DriverSnapshot.driver_id)
        )
    ).scalars()
    vehicles = (
        await session.execute(
            select(VehicleSnapshot)
            .where(VehicleSnapshot.snapshot_id == snapshot_id)
            .order_by(VehicleSnapshot.vehicle_id)
        )
    ).scalars()
    trip_fields = set(TripRecord.model_fields)
    driver_fields = set(DriverRecord.model_fields)
    vehicle_fields = set(VehicleRecord.model_fields)
    return (
        [TripRecord.model_validate({f: getattr(t, f) for f in trip_fields}) for t in trips],
        [DriverRecord.model_validate({f: getattr(d, f) for f in driver_fields}) for d in drivers],
        [VehicleRecord.model_validate({f: getattr(v, f) for f in vehicle_fields}) for v in vehicles],
    )


async def delete_all_snapshots(session: AsyncSession) -> None:
    await session.execute(delete(Snapshot))
