"""intraday_monitor: slack per driver from live positions, HERE only for at risk legs, repairs.

For each driver with a downstream assignment: take the latest ping; if the driver is on a trip
estimate the remaining leg to the drop with OSRM or the fallback (free), add the stored deadhead
p80 to the next pickup; slack = next scheduled pickup minus predicted arrival. Below
RISK_THRESHOLD_S one HERE Routing call with departureTime=now recomputes the legs. Predicted
lateness beyond LATE_TOLERANCE_S triggers an incremental repair that frees the driver and the
float drivers. The trip in progress is never repaired, only downstream ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from app.config import Settings
from app.core.events import publish
from app.core.geo import Point
from app.core.logging import get_logger
from app.core.metrics import MONITOR_AT_RISK
from app.core.timeutil import seconds_since_day_start, service_date_of
from app.db.models import MonitorState, Plan, PlanAssignment
from app.db.queries import plan_assignments, published_plan, trip_live_map, trips_in_snapshot
from app.db.session import session_scope
from app.eta.estimator import LegEstimator
from app.eta.factory import build_estimator
from app.jobs.context import AppContext
from app.jobs.morning import check_absent
from app.jobs.repairs import run_repair
from app.plan.events import latest_pings
from app.plan.repair import RepairRequest

log = get_logger("jobs.monitor")
PING_MAX_AGE = timedelta(minutes=20)
REPAIRED_KEY = "monitor:repaired:{day}"
ON_TRIP = ("started",)
HEADING = ("en_route", "arrived")
DONE = ("completed", "no_show", "cancelled")


@dataclass(slots=True)
class DriverRisk:
    driver_id: uuid.UUID
    current_trip: uuid.UUID | None
    next_trip: uuid.UUID | None
    predicted_arrival: datetime | None
    slack_s: int | None
    risk: str
    source: str | None
    lat: float | None
    lng: float | None
    ping_at: datetime | None
    available_at: tuple[int, Point] | None = None
    details: dict[str, Any] | None = None


def risk_level(slack_s: int | None, late_tolerance_s: int) -> str:
    if slack_s is None:
        return "green"
    if slack_s < -late_tolerance_s or slack_s < 300:
        return "red"
    if slack_s < 900:
        return "amber"
    return "green"


async def intraday_monitor(ctx: AppContext, now: datetime | None = None) -> dict[str, Any]:
    now = now or await ctx.clock.now()
    day = service_date_of(now)
    settings = await ctx.settings()
    async with ctx.factory() as session:
        plan = await published_plan(session, day)
    if plan is None:
        return {"skipped": True, "reason": "no published plan for today", "service_date": day.isoformat()}
    absent = await check_absent(ctx, day, now, plan)
    async with ctx.factory() as session:
        plan = await published_plan(session, day) or plan
    risks = await assess(ctx, settings, plan, now)
    repaired: list[dict[str, Any]] = []
    key = REPAIRED_KEY.format(day=day.isoformat())
    for r in risks:
        if r.slack_s is None or r.next_trip is None or r.slack_s >= -settings.late_tolerance_s:
            continue
        if await ctx.redis.sismember(key, str(r.next_trip)):
            continue
        await ctx.redis.sadd(key, str(r.next_trip))
        await ctx.redis.expire(key, 3 * 86400)
        async with ctx.factory() as session:
            current = await published_plan(session, day) or plan
        outcome = await run_repair(
            ctx,
            current,
            RepairRequest(
                trigger="late_predicted",
                reason=f"driver predicted {-(r.slack_s or 0) // 60} min late for the next pickup",
                actor="job:intraday_monitor",
                free_drivers={r.driver_id},
                now=now,
                trip_ids=[r.next_trip],
                must_keep={r.next_trip},
                available={r.driver_id: r.available_at} if r.available_at else {},
                details={"driver_id": str(r.driver_id), "slack_s": r.slack_s, "source": r.source},
            ),
        )
        moved = (
            r.next_trip in set(outcome.event.trip_ids)
            and outcome.plan is not None
            and await _moved(ctx, outcome.plan, r.next_trip, r.driver_id)
        )
        repaired.append(
            {
                "driver_id": str(r.driver_id),
                "trip_id": str(r.next_trip),
                "outcome": outcome.outcome,
                "trip_moved": moved,
            }
        )
    await _store(ctx, day, now, risks)
    at_risk = sum(1 for r in risks if r.risk != "green")
    MONITOR_AT_RISK.set(at_risk)
    summary = {
        "service_date": day.isoformat(),
        "sim_time": now.isoformat(),
        "plan_id": str(plan.plan_id),
        "drivers": len(risks),
        "at_risk": at_risk,
        "red": sum(1 for r in risks if r.risk == "red"),
        "repairs": repaired,
        "absent": absent,
    }
    await publish(ctx.redis, "monitor.tick", summary)
    return summary


async def _moved(ctx: AppContext, plan: Plan, trip_id: uuid.UUID, driver_id: uuid.UUID) -> bool:
    async with ctx.factory() as session:
        for a in await plan_assignments(session, plan.plan_id):
            if a.trip_id == trip_id:
                return a.driver_id != driver_id
    return False


async def assess(ctx: AppContext, settings: Settings, plan: Plan, now: datetime) -> list[DriverRisk]:
    day = plan.service_date
    free_est = await build_estimator(
        settings, ctx.factory, ctx.redis, ctx.http, use_here=False, persist=False
    )
    live_est = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http, persist=False)
    async with ctx.factory() as session:
        assignments = list(await plan_assignments(session, plan.plan_id))
        trips = {t.trip_id: t for t in await trips_in_snapshot(session, plan.snapshot_id)}
        live = await trip_live_map(session, day)
        pings = await latest_pings(session, now, now - PING_MAX_AGE)
    by_driver: dict[uuid.UUID, list[PlanAssignment]] = {}
    for a in assignments:
        if a.driver_id is not None and a.seq is not None:
            by_driver.setdefault(a.driver_id, []).append(a)
    out: list[DriverRisk] = []
    for driver_id, rows in sorted(by_driver.items(), key=lambda kv: str(kv[0])):
        rows.sort(key=lambda r: r.seq or 0)
        status = {r.trip_id: (live[r.trip_id].status if r.trip_id in live else "assigned") for r in rows}
        current = next((r for r in rows if status[r.trip_id] in ON_TRIP), None)
        upcoming = [r for r in rows if status[r.trip_id] not in (*DONE, *ON_TRIP)]
        ping = pings.get(driver_id)
        if not upcoming:
            out.append(
                DriverRisk(
                    driver_id,
                    current.trip_id if current else None,
                    None,
                    None,
                    None,
                    "green",
                    None,
                    ping.lat if ping else None,
                    ping.lng if ping else None,
                    ping.ts if ping else None,
                )
            )
            continue
        nxt = upcoming[0]
        t_next = trips.get(nxt.trip_id)
        if t_next is None or nxt.planned_pickup_at is None:
            continue
        pickup_pt = Point(t_next.pickup_lat, t_next.pickup_lng)
        where = Point(ping.lat, ping.lng) if ping else None
        arrival: datetime | None = None
        source: str | None = None
        available: tuple[int, Point] | None = None
        legs: list[tuple[Point, Point, str]] = []
        if current is not None and where is not None:
            t_cur = trips.get(current.trip_id)
            if t_cur is not None:
                drop_pt = Point(t_cur.drop_lat, t_cur.drop_lng)
                rem = await free_est.estimate(where, drop_pt, now, "trip")
                free_at = now + timedelta(seconds=rem.p80_s)
                available = (seconds_since_day_start(free_at, day), drop_pt)
                arrival = free_at + timedelta(seconds=nxt.deadhead_s or 0)
                source = rem.source
                legs = [(where, drop_pt, "trip"), (drop_pt, pickup_pt, "deadhead")]
        elif where is not None and (
            status[nxt.trip_id] in HEADING or (nxt.planned_depart_at and nxt.planned_depart_at <= now)
        ):
            leg = await free_est.estimate(where, pickup_pt, now, "deadhead")
            arrival = now + timedelta(seconds=leg.p80_s)
            source = leg.source
            available = (seconds_since_day_start(now, day), where)
            legs = [(where, pickup_pt, "deadhead")]
        elif nxt.planned_arrive_pickup_at is not None:
            arrival = nxt.planned_arrive_pickup_at
            source = "plan"
        slack = int((nxt.planned_pickup_at - arrival).total_seconds()) if arrival else None
        if slack is not None and slack < settings.risk_threshold_s and legs:
            arrival, source = await _here_check(live_est, legs, now, arrival)
            slack = int((nxt.planned_pickup_at - arrival).total_seconds()) if arrival else slack
            if current is not None and arrival is not None and available is not None:
                available = (
                    seconds_since_day_start(arrival - timedelta(seconds=nxt.deadhead_s or 0), day),
                    available[1],
                )
        out.append(
            DriverRisk(
                driver_id=driver_id,
                current_trip=current.trip_id if current else None,
                next_trip=nxt.trip_id,
                predicted_arrival=arrival,
                slack_s=slack,
                risk=risk_level(slack, settings.late_tolerance_s),
                source=source,
                lat=ping.lat if ping else None,
                lng=ping.lng if ping else None,
                ping_at=ping.ts if ping else None,
                available_at=available,
                details={"current_status": status.get(current.trip_id) if current else None},
            )
        )
    return out


async def _here_check(
    est: LegEstimator, legs: list[tuple[Point, Point, str]], now: datetime, fallback: datetime | None
) -> tuple[datetime | None, str | None]:
    """One live HERE Routing call per leg with departureTime=now; the chain degrades if HERE is out."""
    t = now
    source = None
    for origin, dest, kind in legs:
        e = await est.estimate(origin, dest, t, "trip" if kind == "trip" else "deadhead", live=True)
        t = t + timedelta(seconds=e.p80_s)
        source = e.source
    return (t if source else fallback), source


async def _store(ctx: AppContext, day: date, now: datetime, risks: list[DriverRisk]) -> None:
    async with session_scope(ctx.factory) as session:
        for r in risks:
            values = {
                "service_date": day,
                "driver_id": r.driver_id,
                "computed_at": datetime.now(now.tzinfo),
                "sim_time": now,
                "lat": r.lat,
                "lng": r.lng,
                "ping_at": r.ping_at,
                "current_trip_id": r.current_trip,
                "next_trip_id": r.next_trip,
                "predicted_arrival_at": r.predicted_arrival,
                "slack_s": r.slack_s,
                "risk": r.risk,
                "source": r.source,
                "details": r.details or {},
            }
            stmt = insert(MonitorState).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[MonitorState.service_date, MonitorState.driver_id],
                set_={k: stmt.excluded[k] for k in values if k not in ("service_date", "driver_id")},
            )
            await session.execute(stmt)
