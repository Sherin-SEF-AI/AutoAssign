"""Plans: versions, detail by driver, solve, moves, locks, publish, diff, unassigned, repairs."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Ctx, EffectiveSettings, Session, User
from app.core.errors import Unprocessable
from app.core.timeutil import service_date_of
from app.db.models import Plan, RepairEvent, UnassignedAck
from app.db.queries import (
    drivers_in_snapshot,
    plan_assignments,
    plan_routes,
    plans_for_date,
    trip_live_map,
    trips_in_snapshot,
    vehicles_in_snapshot,
)
from app.eta.factory import build_estimator
from app.jobs.repairs import run_repair
from app.jobs.solve import solve_date
from app.plan.repair import RepairRequest
from app.plan.service import move_trip, notify_plan, publish_and_notify, set_lock
from app.plan.versions import acknowledge, diff, get_plan

router = APIRouter(tags=["plans"])

PlanStatus = Literal["draft", "published", "superseded"]


class PlanSummary(BaseModel):
    plan_id: uuid.UUID
    service_date: date
    version: int
    status: PlanStatus
    parent_plan_id: uuid.UUID | None
    snapshot_id: uuid.UUID
    trigger: str
    reason: str
    created_by: str
    created_at: datetime
    published_at: datetime | None
    kpis: dict[str, Any]
    solver_params: dict[str, Any]
    solver_stats: dict[str, Any]


def summary(p: Plan) -> PlanSummary:
    return PlanSummary(
        plan_id=p.plan_id,
        service_date=p.service_date,
        version=p.version,
        status=p.status,
        parent_plan_id=p.parent_plan_id,
        snapshot_id=p.snapshot_id,
        trigger=p.trigger,
        reason=p.reason,
        created_by=p.created_by,
        created_at=p.created_at,
        published_at=p.published_at,
        kpis=dict(p.solver_stats.get("kpis", {})),
        solver_params=p.solver_params,
        solver_stats={k: v for k, v in p.solver_stats.items() if k != "kpis"},
    )


class AssignmentOut(BaseModel):
    trip_id: uuid.UUID
    driver_id: uuid.UUID | None
    vehicle_id: uuid.UUID | None
    seq: int | None
    planned_depart_at: datetime | None
    planned_arrive_pickup_at: datetime | None
    planned_pickup_at: datetime | None
    planned_drop_at: datetime | None
    deadhead_s: int | None
    deadhead_m: int | None
    buffer_s: int | None
    trip_s: int | None
    trip_m: int | None
    trip_p50_s: int | None
    slack_s: int | None
    energy_wh: int | None
    trip_source: str | None
    deadhead_source: str | None
    locked: bool
    unassigned_reason: str | None
    acknowledged: bool = False
    scheduled_pickup_at: datetime | None = None
    trip_type: str | None = None
    channel: str | None = None
    vehicle_class: str | None = None
    account_id: str | None = None
    vip: bool = False
    pickup_address: str | None = None
    drop_address: str | None = None
    pickup_lat: float | None = None
    pickup_lng: float | None = None
    drop_lat: float | None = None
    drop_lng: float | None = None
    status: str | None = None


class RouteOut(BaseModel):
    trip_count: int
    start_at: datetime | None
    end_at: datetime | None
    end_leg_s: int
    energy_wh: int
    energy_cap_wh: int
    soc_fraction: float
    distance_m: int


class DriverLane(BaseModel):
    driver_id: uuid.UUID
    name: str
    vehicle_id: uuid.UUID | None
    vehicle_registration: str | None
    vehicle_model: str | None
    vehicle_class: str | None
    shift_start_at: datetime | None
    shift_end_at: datetime | None
    skills: list[str]
    approved_accounts: list[str]
    available: bool
    unavailable_reason: str | None
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float
    route: RouteOut | None
    assignments: list[AssignmentOut]


class PlanDetail(BaseModel):
    plan: PlanSummary
    drivers: list[DriverLane]
    unassigned: list[AssignmentOut]


@router.get("/plans", response_model=list[PlanSummary])
async def list_plans(
    session: Session, _: User, date_: Annotated[date, Query(alias="date")]
) -> list[PlanSummary]:
    return [summary(p) for p in await plans_for_date(session, date_)]


@router.get("/plans/{plan_id}", response_model=PlanDetail)
async def get_plan_detail(plan_id: uuid.UUID, session: Session, _: User) -> PlanDetail:
    plan = await get_plan(session, plan_id)
    trips = {t.trip_id: t for t in await trips_in_snapshot(session, plan.snapshot_id)}
    drivers = await drivers_in_snapshot(session, plan.snapshot_id)
    vehicles = {v.vehicle_id: v for v in await vehicles_in_snapshot(session, plan.snapshot_id)}
    live = await trip_live_map(session, plan.service_date)
    acks = {
        (a.trip_id, a.reason)
        for a in (
            await session.execute(
                select(UnassignedAck).where(UnassignedAck.service_date == plan.service_date)
            )
        ).scalars()
    }
    routes = {r.driver_id: r for r in await plan_routes(session, plan.plan_id)}
    lanes: dict[uuid.UUID, list[AssignmentOut]] = {}
    unassigned: list[AssignmentOut] = []
    for a in await plan_assignments(session, plan.plan_id):
        t = trips.get(a.trip_id)
        lv = live.get(a.trip_id)
        out = AssignmentOut.model_validate(a, from_attributes=True)
        if t is not None:
            out = out.model_copy(
                update={
                    "scheduled_pickup_at": t.scheduled_pickup_at,
                    "trip_type": t.trip_type,
                    "channel": t.channel,
                    "vehicle_class": t.vehicle_class,
                    "account_id": t.account_id,
                    "vip": bool(t.tags.get("vip")),
                    "pickup_address": t.pickup_address,
                    "drop_address": t.drop_address,
                    "pickup_lat": t.pickup_lat,
                    "pickup_lng": t.pickup_lng,
                    "drop_lat": t.drop_lat,
                    "drop_lng": t.drop_lng,
                    "status": lv.status if lv else t.status,
                }
            )
        if a.driver_id is None:
            out.acknowledged = (a.trip_id, a.unassigned_reason) in acks
            unassigned.append(out)
        else:
            lanes.setdefault(a.driver_id, []).append(out)
    out_lanes: list[DriverLane] = []
    for d in drivers:
        v = vehicles.get(d.vehicle_id) if d.vehicle_id else None
        reason = None
        if not d.active:
            reason = "off_duty"
        elif d.channel_today == "uber":
            reason = "uber_shift"
        elif v is None or v.status != "active":
            reason = "vehicle_unavailable"
        if reason == "off_duty" and d.driver_id not in lanes:
            continue
        route = routes.get(d.driver_id)
        out_lanes.append(
            DriverLane(
                driver_id=d.driver_id,
                name=d.name,
                vehicle_id=d.vehicle_id,
                vehicle_registration=v.registration if v else None,
                vehicle_model=v.variant if v else None,
                vehicle_class=v.vehicle_class if v else None,
                shift_start_at=d.shift_start_at,
                shift_end_at=d.shift_end_at,
                skills=list(d.skills),
                approved_accounts=list(d.approved_accounts),
                available=reason is None,
                unavailable_reason=reason,
                start_lat=d.start_lat,
                start_lng=d.start_lng,
                end_lat=d.end_lat,
                end_lng=d.end_lng,
                route=RouteOut.model_validate(route, from_attributes=True) if route else None,
                assignments=sorted(lanes.get(d.driver_id, []), key=lambda x: x.seq or 0),
            )
        )
    out_lanes.sort(
        key=lambda lane: (
            not lane.available,
            lane.shift_start_at.timestamp() if lane.shift_start_at else float("inf"),
            lane.name,
        )
    )
    unassigned.sort(key=lambda x: x.scheduled_pickup_at.timestamp() if x.scheduled_pickup_at else 0)
    return PlanDetail(plan=summary(plan), drivers=out_lanes, unassigned=unassigned)


class SolveIn(BaseModel):
    service_date: date


@router.post("/plans/solve", response_model=PlanSummary, status_code=201)
async def solve_plan(body: SolveIn, ctx: Ctx, user: User) -> PlanSummary:
    plan, _ = await solve_date(ctx, body.service_date, created_by=user.subject, trigger="on_demand")
    assert plan is not None
    return summary(plan)


class MoveIn(BaseModel):
    trip_id: uuid.UUID
    to_driver_id: uuid.UUID | None = None
    reason: str = Field(default="", max_length=500)
    force: bool = False
    dry_run: bool = False


class IssueOut(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str
    trip_id: str | None
    driver_id: str | None


class PreviewStop(BaseModel):
    trip_id: str
    seq: int
    planned_depart_at: str
    planned_arrive_pickup_at: str
    planned_pickup_at: str
    planned_drop_at: str
    slack_s: int
    deadhead_s: int
    buffer_s: int


class DriverPreviewOut(BaseModel):
    driver_id: uuid.UUID | None
    stops: list[PreviewStop]
    energy_wh: int
    energy_cap_wh: int
    end_at: str | None


class MoveOut(BaseModel):
    accepted: bool
    forced: bool
    errors: list[IssueOut]
    warnings: list[IssueOut]
    drivers: list[DriverPreviewOut]
    plan: PlanSummary | None
    override_id: uuid.UUID | None
    message: str


@router.post("/plans/{plan_id}/moves", response_model=MoveOut)
async def move(
    plan_id: uuid.UUID, body: MoveIn, session: Session, ctx: Ctx, settings: EffectiveSettings, user: User
) -> MoveOut:
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http)
    outcome = await move_trip(
        session,
        settings,
        estimator,
        plan_id,
        trip_id=body.trip_id,
        to_driver_id=body.to_driver_id,
        reason=body.reason,
        force=body.force,
        actor=user.subject,
        dry_run=body.dry_run,
    )
    await session.commit()
    if outcome.plan is not None:
        await notify_plan(ctx.notifier, "plan.updated", outcome.plan, override_id=str(outcome.override_id))
    return MoveOut(
        accepted=outcome.accepted,
        forced=outcome.forced,
        errors=[IssueOut(**e) for e in outcome.errors],
        warnings=[IssueOut(**w) for w in outcome.warnings],
        drivers=[DriverPreviewOut(**asdict(p)) for p in outcome.drivers],
        plan=summary(outcome.plan) if outcome.plan else None,
        override_id=outcome.override_id,
        message=outcome.message,
    )


async def _lock(
    plan_id: uuid.UUID, trip_id: uuid.UUID, locked: bool, session: Any, ctx: Any, settings: Any, user: Any
) -> PlanSummary:
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http)
    plan = await set_lock(session, settings, estimator, plan_id, trip_id, locked=locked, actor=user.subject)
    await session.commit()
    await notify_plan(ctx.notifier, "plan.updated", plan)
    return summary(plan)


@router.post("/plans/{plan_id}/assignments/{trip_id}/lock", response_model=PlanSummary, status_code=201)
async def lock(
    plan_id: uuid.UUID,
    trip_id: uuid.UUID,
    session: Session,
    ctx: Ctx,
    settings: EffectiveSettings,
    user: User,
) -> PlanSummary:
    return await _lock(plan_id, trip_id, True, session, ctx, settings, user)


@router.delete("/plans/{plan_id}/assignments/{trip_id}/lock", response_model=PlanSummary)
async def unlock(
    plan_id: uuid.UUID,
    trip_id: uuid.UUID,
    session: Session,
    ctx: Ctx,
    settings: EffectiveSettings,
    user: User,
) -> PlanSummary:
    return await _lock(plan_id, trip_id, False, session, ctx, settings, user)


@router.post("/plans/{plan_id}/publish", response_model=PlanSummary)
async def publish_plan(plan_id: uuid.UUID, session: Session, ctx: Ctx, _: User) -> PlanSummary:
    plan = await get_plan(session, plan_id)
    await publish_and_notify(session, ctx.notifier, plan)
    return summary(plan)


class TripDiffOut(BaseModel):
    trip_id: uuid.UUID
    driver_before: uuid.UUID | None
    driver_after: uuid.UUID | None
    pickup_shift_min: float | None
    changed: bool


class DriverDiffOut(BaseModel):
    driver_id: uuid.UUID
    gained: int
    lost: int


class DiffOut(BaseModel):
    plan_id: uuid.UUID
    against: uuid.UUID
    trips: list[TripDiffOut]
    drivers: list[DriverDiffOut]
    changed_trips: int


@router.get("/plans/{plan_id}/diff", response_model=DiffOut)
async def plan_diff(plan_id: uuid.UUID, session: Session, _: User, against: uuid.UUID) -> DiffOut:
    plan = await get_plan(session, plan_id)
    other = await get_plan(session, against)
    if plan.service_date != other.service_date:
        raise Unprocessable("plans belong to different service dates", code="different_dates")
    trips, per_driver = await diff(session, plan, other)
    return DiffOut(
        plan_id=plan_id,
        against=against,
        trips=[TripDiffOut(**asdict(t)) for t in trips if t.changed],
        drivers=[
            DriverDiffOut(driver_id=d, **v) for d, v in sorted(per_driver.items(), key=lambda kv: str(kv[0]))
        ],
        changed_trips=sum(1 for t in trips if t.changed),
    )


@router.get("/plans/{plan_id}/unassigned", response_model=list[AssignmentOut])
async def unassigned(plan_id: uuid.UUID, session: Session, user: User) -> list[AssignmentOut]:
    detail = await get_plan_detail(plan_id, session, user)
    return detail.unassigned


class AckIn(BaseModel):
    trip_ids: list[uuid.UUID] | None = None
    note: str = Field(default="", max_length=500)


class AckOut(BaseModel):
    acknowledged: int


@router.post("/plans/{plan_id}/unassigned/acknowledge", response_model=AckOut)
async def acknowledge_unassigned(plan_id: uuid.UUID, body: AckIn, session: Session, user: User) -> AckOut:
    plan = await get_plan(session, plan_id)
    n = await acknowledge(session, plan, user.subject, body.trip_ids, body.note)
    return AckOut(acknowledged=n)


class RepairOut(BaseModel):
    id: uuid.UUID
    service_date: date
    trigger: str
    outcome: str
    plan_id_before: uuid.UUID | None
    plan_id_after: uuid.UUID | None
    trip_ids: list[uuid.UUID]
    details: dict[str, Any]
    created_at: datetime


@router.get("/repairs", response_model=list[RepairOut])
async def repairs(session: Session, _: User, date_: Annotated[date, Query(alias="date")]) -> list[RepairOut]:
    rows = (
        await session.execute(
            select(RepairEvent)
            .where(RepairEvent.service_date == date_)
            .order_by(RepairEvent.created_at.desc())
        )
    ).scalars()
    return [RepairOut.model_validate(r, from_attributes=True) for r in rows]


class ResolveIn(BaseModel):
    driver_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)
    reason: str = Field(default="manual re-solve", max_length=500)
    include_float: bool = True


class ResolveOut(BaseModel):
    outcome: str
    plan: PlanSummary | None
    repair_id: uuid.UUID
    freed: list[uuid.UUID]
    unassigned: list[uuid.UUID]


@router.post("/plans/{plan_id}/resolve", response_model=ResolveOut)
async def resolve_drivers(
    plan_id: uuid.UUID, body: ResolveIn, session: Session, ctx: Ctx, user: User
) -> ResolveOut:
    """Incremental re-solve of the named drivers (and float drivers); everyone else stays locked."""
    plan = await get_plan(session, plan_id)
    if plan.status == "superseded":
        raise Unprocessable("re-solve the current version, not a superseded one", code="superseded")
    now = await ctx.clock.now()
    outcome = await run_repair(
        ctx,
        plan,
        RepairRequest(
            trigger="manual",
            reason=body.reason,
            actor=user.subject,
            free_drivers=set(body.driver_ids),
            include_float=body.include_float,
            now=now if service_date_of(now) == plan.service_date else None,
        ),
    )
    return ResolveOut(
        outcome=outcome.outcome,
        plan=summary(outcome.plan) if outcome.plan else None,
        repair_id=outcome.event.id,
        freed=outcome.freed,
        unassigned=outcome.unassigned,
    )
