"""Service to service ingestion (trip events, pings), the live view and simulator controls."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.base import PingRecord, TripEvent
from app.api.deps import Admin, Ctx, EffectiveSettings, Service, Session, User
from app.core.clock import SimState, load_sim_state, save_sim_state
from app.core.errors import Unprocessable
from app.core.events import publish
from app.core.timeutil import ist_at, service_date_of
from app.db.models import MonitorState
from app.jobs.late_booking import enqueue
from app.plan.events import apply_trip_event, insert_pings

router = APIRouter(tags=["ops"])

SIM_CONTROL_KEY = "sim:control"
SIM_HEARTBEAT_KEY = "sim:heartbeat"
DISTURBANCES = ("slow_legs", "no_show_driver", "low_soc", "late_bookings", "cancellation")


class EventAccepted(BaseModel):
    event_id: str
    duplicate: bool
    snapshot_id: uuid.UUID | None
    replan_queued: bool


@router.post("/events/trip", response_model=EventAccepted, status_code=202)
async def trip_event(
    event: TripEvent, session: Session, ctx: Ctx, settings: EffectiveSettings, _: Service
) -> EventAccepted:
    result = await apply_trip_event(session, settings, event)
    await session.commit()
    queued = False
    if not result.duplicate and result.replan:
        await enqueue(ctx.redis, result)
        queued = True
    return EventAccepted(
        event_id=result.event_id,
        duplicate=result.duplicate,
        snapshot_id=result.snapshot_id,
        replan_queued=queued,
    )


class PingBatch(BaseModel):
    pings: list[PingRecord] = Field(max_length=5000)


class PingsAccepted(BaseModel):
    received: int
    stored: int


@router.post("/pings", response_model=PingsAccepted, status_code=202)
async def pings(body: PingBatch, session: Session, settings: EffectiveSettings, _: Service) -> PingsAccepted:
    if len(body.pings) > settings.pings_max_batch:
        raise Unprocessable(f"at most {settings.pings_max_batch} pings per batch", code="batch_too_large")
    stored = await insert_pings(session, body.pings)
    return PingsAccepted(received=len(body.pings), stored=stored)


class LiveDriver(BaseModel):
    driver_id: uuid.UUID
    computed_at: datetime
    sim_time: datetime
    lat: float | None
    lng: float | None
    ping_at: datetime | None
    current_trip_id: uuid.UUID | None
    next_trip_id: uuid.UUID | None
    predicted_arrival_at: datetime | None
    slack_s: int | None
    risk: str
    source: str | None
    details: dict[str, Any]


class LiveOut(BaseModel):
    service_date: date
    as_of: datetime | None
    drivers: list[LiveDriver]


@router.get("/live", response_model=LiveOut)
async def live(session: Session, _: User, date_: Annotated[date, Query(alias="date")]) -> LiveOut:
    rows = (
        (
            await session.execute(
                select(MonitorState)
                .where(MonitorState.service_date == date_)
                .order_by(MonitorState.driver_id)
            )
        )
        .scalars()
        .all()
    )
    as_of = max((r.sim_time for r in rows), default=None)
    return LiveOut(
        service_date=date_,
        as_of=as_of,
        drivers=[LiveDriver.model_validate(r, from_attributes=True) for r in rows],
    )


class SimStatus(BaseModel):
    exists: bool
    running: bool
    service_date: date | None
    sim_time: datetime | None
    speed: float | None
    disturbances: dict[str, bool]
    process_alive: bool
    stats: dict[str, Any] = Field(default_factory=dict)


async def _status(ctx: Any) -> SimStatus:
    state = await load_sim_state(ctx.redis)
    beat = await ctx.redis.get(SIM_HEARTBEAT_KEY)
    stats_raw = await ctx.redis.get("sim:stats")

    if state is None:
        return SimStatus(
            exists=False,
            running=False,
            service_date=None,
            sim_time=None,
            speed=None,
            disturbances=dict.fromkeys(DISTURBANCES, True),
            process_alive=beat is not None,
        )
    return SimStatus(
        exists=True,
        running=state.running,
        service_date=date.fromisoformat(state.service_date),
        sim_time=state.sim_now(datetime.now(UTC)),
        speed=state.speed,
        disturbances={k: bool(state.disturbances.get(k, False)) for k in DISTURBANCES},
        process_alive=beat is not None,
        stats=json.loads(stats_raw) if stats_raw else {},
    )


class SimStartIn(BaseModel):
    service_date: date | None = None
    speed: float | None = Field(default=None, gt=0, le=10000)


@router.get("/admin/sim/status", response_model=SimStatus)
async def sim_status(ctx: Ctx, _: User) -> SimStatus:
    return await _status(ctx)


@router.post("/admin/sim/start", response_model=SimStatus)
async def sim_start(
    ctx: Ctx, settings: EffectiveSettings, _: Admin, body: SimStartIn | None = None
) -> SimStatus:
    now = datetime.now(UTC)
    state = await load_sim_state(ctx.redis)
    if state is None:
        day = (body.service_date if body and body.service_date else None) or (
            date.fromisoformat(settings.sim_date) if settings.sim_date else service_date_of(now)
        )
        state = SimState(
            service_date=day.isoformat(),
            running=True,
            speed=(body.speed if body and body.speed else settings.sim_speed),
            anchor_sim=ist_at(day, 4, 30),
            anchor_wall=now,
            disturbances=dict.fromkeys(DISTURBANCES, True),
        )
    else:
        state.anchor_sim = state.sim_now(now)
        state.anchor_wall = now
        state.running = True
        if body and body.speed:
            state.speed = body.speed
    await save_sim_state(ctx.redis, state)
    await publish(
        ctx.redis,
        "sim.clock",
        {
            "sim_time": state.anchor_sim.isoformat(),
            "running": True,
            "speed": state.speed,
            "service_date": state.service_date,
        },
    )
    return await _status(ctx)


@router.post("/admin/sim/pause", response_model=SimStatus)
async def sim_pause(ctx: Ctx, _: Admin) -> SimStatus:
    state = await load_sim_state(ctx.redis)
    if state is not None and state.running:
        now = datetime.now(UTC)
        state.anchor_sim = state.sim_now(now)
        state.anchor_wall = now
        state.running = False
        await save_sim_state(ctx.redis, state)
        await publish(
            ctx.redis,
            "sim.clock",
            {
                "sim_time": state.anchor_sim.isoformat(),
                "running": False,
                "speed": state.speed,
                "service_date": state.service_date,
            },
        )
    return await _status(ctx)


@router.post("/admin/sim/reset", response_model=SimStatus)
async def sim_reset(ctx: Ctx, _: Admin) -> SimStatus:
    """Stops the simulation and returns every clock to wall time. The sim process clears its run."""
    await ctx.redis.delete("sim:state", "sim:stats")
    await ctx.redis.set(SIM_CONTROL_KEY, "reset", ex=600)
    await publish(
        ctx.redis, "sim.clock", {"sim_time": None, "running": False, "speed": None, "service_date": None}
    )
    return await _status(ctx)


class DisturbancesIn(BaseModel):
    slow_legs: bool | None = None
    no_show_driver: bool | None = None
    low_soc: bool | None = None
    late_bookings: bool | None = None
    cancellation: bool | None = None


@router.post("/admin/sim/disturbances", response_model=SimStatus)
async def sim_disturbances(body: DisturbancesIn, ctx: Ctx, _: Admin) -> SimStatus:
    state = await load_sim_state(ctx.redis)
    if state is None:
        raise Unprocessable("start a simulation first", code="sim_not_started")
    for k, v in body.model_dump(exclude_none=True).items():
        state.disturbances[k] = v
    await save_sim_state(ctx.redis, state)
    return await _status(ctx)
