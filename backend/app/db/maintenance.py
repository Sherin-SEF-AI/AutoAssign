"""Dataset level maintenance operations."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DATASET_TABLES = (
    "plan_assignment",
    "plan_route",
    "override",
    "unassigned_ack",
    "repair_event",
    "plan",
    "trip_live",
    "inbound_event",
    "eta_log",
    "soc_checkin",
    "monitor_state",
    "ping",
    "trip_snapshot",
    "driver_snapshot",
    "vehicle_snapshot",
    "snapshot",
    "leg_estimate",
    "factor_table",
    "zone_cluster",
    "replay_run",
)


async def reset_dataset(session: AsyncSession) -> None:
    """Remove every operational row while keeping users, settings and job history."""
    await session.execute(text("TRUNCATE " + ", ".join(DATASET_TABLES) + " RESTART IDENTITY CASCADE"))
