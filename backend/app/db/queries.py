"""Read helpers shared by services and routers."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DriverSnapshot,
    Hub,
    Plan,
    PlanAssignment,
    PlanRoute,
    Snapshot,
    SocCheckin,
    TripLive,
    TripSnapshot,
    VehicleSnapshot,
)


async def trips_in_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> Sequence[TripSnapshot]:
    stmt = (
        select(TripSnapshot)
        .where(TripSnapshot.snapshot_id == snapshot_id)
        .order_by(TripSnapshot.scheduled_pickup_at, TripSnapshot.trip_id)
    )
    return (await session.execute(stmt)).scalars().all()


async def drivers_in_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> Sequence[DriverSnapshot]:
    stmt = (
        select(DriverSnapshot)
        .where(DriverSnapshot.snapshot_id == snapshot_id)
        .order_by(DriverSnapshot.driver_id)
    )
    return (await session.execute(stmt)).scalars().all()


async def vehicles_in_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> Sequence[VehicleSnapshot]:
    stmt = (
        select(VehicleSnapshot)
        .where(VehicleSnapshot.snapshot_id == snapshot_id)
        .order_by(VehicleSnapshot.vehicle_id)
    )
    return (await session.execute(stmt)).scalars().all()


async def latest_vehicle_snapshot_id(session: AsyncSession) -> uuid.UUID | None:
    stmt = (
        select(Snapshot.snapshot_id)
        .where(Snapshot.counts["vehicles"].as_integer() > 0)
        .order_by(Snapshot.service_date.desc(), Snapshot.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def trip_live_map(session: AsyncSession, service_date: date) -> dict[uuid.UUID, TripLive]:
    rows = (await session.execute(select(TripLive).where(TripLive.service_date == service_date))).scalars()
    return {r.trip_id: r for r in rows}


async def soc_map(session: AsyncSession, service_date: date) -> dict[uuid.UUID, SocCheckin]:
    rows = (
        await session.execute(select(SocCheckin).where(SocCheckin.service_date == service_date))
    ).scalars()
    return {r.vehicle_id: r for r in rows}


async def list_hubs(session: AsyncSession) -> Sequence[Hub]:
    return (await session.execute(select(Hub).order_by(Hub.name))).scalars().all()


async def plans_for_date(session: AsyncSession, service_date: date) -> Sequence[Plan]:
    stmt = select(Plan).where(Plan.service_date == service_date).order_by(Plan.version.desc())
    return (await session.execute(stmt)).scalars().all()


async def current_plan(session: AsyncSession, service_date: date) -> Plan | None:
    """The published plan for the date, or else the newest version."""
    published = (
        await session.execute(
            select(Plan).where(Plan.service_date == service_date, Plan.status == "published")
        )
    ).scalar_one_or_none()
    if published is not None:
        return published
    stmt = select(Plan).where(Plan.service_date == service_date).order_by(Plan.version.desc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def published_plan(session: AsyncSession, service_date: date) -> Plan | None:
    stmt = select(Plan).where(Plan.service_date == service_date, Plan.status == "published")
    return (await session.execute(stmt)).scalar_one_or_none()


async def plan_assignments(session: AsyncSession, plan_id: uuid.UUID) -> Sequence[PlanAssignment]:
    stmt = (
        select(PlanAssignment)
        .where(PlanAssignment.plan_id == plan_id)
        .order_by(PlanAssignment.driver_id.nulls_last(), PlanAssignment.seq, PlanAssignment.trip_id)
    )
    return (await session.execute(stmt)).scalars().all()


async def plan_routes(session: AsyncSession, plan_id: uuid.UUID) -> Sequence[PlanRoute]:
    stmt = select(PlanRoute).where(PlanRoute.plan_id == plan_id).order_by(PlanRoute.driver_id)
    return (await session.execute(stmt)).scalars().all()


async def list_snapshots(
    session: AsyncSession, service_date: date | None, limit: int = 200
) -> Sequence[Snapshot]:
    stmt = select(Snapshot).order_by(Snapshot.service_date.desc(), Snapshot.created_at.desc()).limit(limit)
    if service_date is not None:
        stmt = stmt.where(Snapshot.service_date == service_date)
    return (await session.execute(stmt)).scalars().all()
