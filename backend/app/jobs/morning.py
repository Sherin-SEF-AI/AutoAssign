"""morning_validate (06:00 IST): SOC check-ins against the plan, absent drivers, repairs."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.core.timeutil import day_start_utc
from app.db.models import Plan
from app.db.queries import drivers_in_snapshot, plan_assignments, plan_routes, published_plan, soc_map
from app.db.session import session_scope
from app.db.snapshots import upsert_soc_checkins
from app.jobs.context import AppContext
from app.jobs.repairs import run_repair
from app.plan.events import drivers_with_pings
from app.plan.repair import RepairRequest
from app.plan.versions import latest_plan

log = get_logger("jobs.morning")
ABSENT_KEY = "absent:{day}"
PING_LOOKBACK = timedelta(hours=2)


async def current_operative_plan(ctx: AppContext, service_date: date) -> Plan | None:
    async with ctx.factory() as session:
        return await published_plan(session, service_date) or await latest_plan(session, service_date)


async def morning_validate(
    ctx: AppContext, service_date: date, now: datetime | None = None
) -> dict[str, Any]:
    now = now or await ctx.clock.now()
    settings = await ctx.settings()
    checkins = await ctx.source.list_soc_checkins(service_date)
    async with session_scope(ctx.factory) as session:
        await upsert_soc_checkins(session, checkins)
    plan = await current_operative_plan(ctx, service_date)
    if plan is None:
        return {"skipped": True, "reason": "no plan for the date", "checkins": len(checkins)}
    async with ctx.factory() as session:
        socs = {k: v.soc_pct for k, v in (await soc_map(session, service_date)).items()}
        routes = list(await plan_routes(session, plan.plan_id))
    shortfall: list[uuid.UUID] = []
    details: dict[str, Any] = {}
    for r in routes:
        soc = socs.get(r.vehicle_id)
        if soc is None or r.soc_fraction <= 0:
            continue
        full_cap = r.energy_cap_wh / r.soc_fraction
        cap = full_cap * soc / 100.0
        if r.energy_wh > cap:
            shortfall.append(r.driver_id)
            details[str(r.driver_id)] = {"soc_pct": soc, "planned_wh": r.energy_wh, "cap_wh": round(cap)}
    stats: dict[str, Any] = {
        "plan_id": str(plan.plan_id),
        "checkins": len(checkins),
        "low_soc": sum(1 for v in socs.values() if v < 100),
        "soc_shortfall": [str(d) for d in shortfall],
    }
    if shortfall:
        outcome = await run_repair(
            ctx,
            plan,
            RepairRequest(
                trigger="soc_shortfall",
                reason=f"SOC check-in below plan for {len(shortfall)} vehicles",
                actor="job:morning_validate",
                free_drivers=set(shortfall),
                soc_pct=socs,
                now=now,
                details={"vehicles": details},
            ),
        )
        stats["soc_repair"] = outcome.outcome
        plan = outcome.plan or plan
    stats["absent"] = await check_absent(ctx, service_date, now, plan)
    _ = settings
    return stats


async def check_absent(
    ctx: AppContext, service_date: date, now: datetime, plan: Plan | None = None
) -> dict[str, Any]:
    """Drivers with work who have not pinged by shift start plus ABSENT_GRACE_S are marked absent."""
    settings = await ctx.settings()
    plan = plan or await current_operative_plan(ctx, service_date)
    if plan is None:
        return {"skipped": True}
    day0 = day_start_utc(service_date)
    async with ctx.factory() as session:
        if not await drivers_with_pings(session, day0, now):
            # No ping feed for the date yet: absence cannot be judged.
            return {"skipped": True, "reason": "no pings for the date"}
        drivers = {d.driver_id: d for d in await drivers_in_snapshot(session, plan.snapshot_id)}
        working = {
            a.driver_id for a in await plan_assignments(session, plan.plan_id) if a.driver_id is not None
        }
        key = ABSENT_KEY.format(day=service_date.isoformat())
        known = {uuid.UUID(x.decode() if isinstance(x, bytes) else x) for x in await ctx.redis.smembers(key)}
        due = []
        for d in sorted(working, key=str):
            start = drivers[d].shift_start_at if d in drivers else None
            if d in known or start is None:
                continue
            if start + timedelta(seconds=settings.absent_grace_s) <= now:
                due.append(d)
        absent: set[uuid.UUID] = set()
        for d in due:
            start = drivers[d].shift_start_at
            assert start is not None
            pinged = await drivers_with_pings(session, start - PING_LOOKBACK, now)
            if d not in pinged:
                absent.add(d)
    if not absent:
        return {"absent": []}
    await ctx.redis.sadd(key, *[str(d) for d in absent])
    await ctx.redis.expire(key, 3 * 86400)
    outcome = await run_repair(
        ctx,
        plan,
        RepairRequest(
            trigger="absent_driver",
            reason=f"{len(absent)} drivers did not start their shift",
            actor="job:absent_check",
            absent_drivers=absent,
            now=now,
            trip_ids=[],
            details={"drivers": sorted(str(d) for d in absent)},
        ),
    )
    return {"absent": sorted(str(d) for d in absent), "outcome": outcome.outcome}
