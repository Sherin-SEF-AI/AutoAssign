"""Ingest: pull a service date from the DataSource into a new snapshot."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.db.session import session_scope
from app.db.snapshots import upsert_hubs, write_snapshot
from app.jobs.context import AppContext
from app.plan.exceptions import apply_fleet_exceptions


async def ingest_date(ctx: AppContext, service_date: date) -> dict[str, Any]:
    settings = await ctx.settings()
    source = ctx.source
    trips = await source.list_trips(service_date)
    drivers = await source.list_drivers(service_date)
    vehicles = await source.list_vehicles()
    hubs = await source.list_hubs()
    async with session_scope(ctx.factory) as session:
        drivers, vehicles = await apply_fleet_exceptions(session, service_date, drivers, vehicles)
        await upsert_hubs(session, hubs)
        write = await write_snapshot(
            session,
            settings,
            service_date=service_date,
            source=source.name,
            trips=trips,
            drivers=drivers,
            vehicles=vehicles,
            reason="ingest",
        )
    return {
        "service_date": service_date.isoformat(),
        "snapshot_id": str(write.snapshot_id),
        "created": write.created,
        **write.counts,
        "hubs": len(hubs),
    }
