"""Append only plan versions: writing, KPIs, publishing and diffs.

Rows are never updated except for the plan status transition on publish.
"""

from __future__ import annotations

import uuid
import zlib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.metrics import PLAN_BUFFER_PER_TRIP, PLAN_DEADHEAD_PER_TRIP, PLAN_UNASSIGNED
from app.core.timeutil import day_start_utc
from app.db.models import Plan, PlanAssignment, PlanRoute, UnassignedAck
from app.graph.model import Graph
from app.solver.evaluate import RouteEval

ASSIGNMENT_FIELDS = (
    "trip_id",
    "driver_id",
    "vehicle_id",
    "seq",
    "planned_depart_at",
    "planned_arrive_pickup_at",
    "planned_pickup_at",
    "planned_drop_at",
    "deadhead_s",
    "deadhead_m",
    "buffer_s",
    "trip_s",
    "trip_m",
    "trip_p50_s",
    "slack_s",
    "energy_wh",
    "trip_source",
    "deadhead_source",
    "locked",
    "unassigned_reason",
)
ROUTE_FIELDS = (
    "driver_id",
    "vehicle_id",
    "trip_count",
    "start_at",
    "end_at",
    "end_leg_s",
    "end_leg_m",
    "energy_wh",
    "energy_cap_wh",
    "soc_fraction",
    "distance_m",
)


@dataclass(slots=True)
class PlanDraft:
    service_date: date
    snapshot_id: uuid.UUID
    trigger: str
    reason: str
    created_by: str
    parent_plan_id: uuid.UUID | None = None
    status: str = "draft"
    solver_params: dict[str, Any] = field(default_factory=dict)
    solver_stats: dict[str, Any] = field(default_factory=dict)
    assignments: list[dict[str, Any]] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)


def _at(day0: datetime, seconds: int) -> datetime:
    return day0 + timedelta(seconds=int(seconds))


def rows_from_eval(
    graph: Graph, ev: RouteEval, *, locked: set[uuid.UUID], seq_offset: int = 0
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    day0 = day_start_utc(graph.service_date)
    v = graph.vehicles[ev.vehicle]
    rows = []
    for stop in ev.stops:
        node = graph.nodes[stop.node]
        rows.append(
            {
                "trip_id": node.trip_id,
                "driver_id": v.driver_id,
                "vehicle_id": v.vehicle_id,
                "seq": seq_offset + stop.seq,
                "planned_depart_at": _at(day0, stop.depart_s),
                "planned_arrive_pickup_at": _at(day0, stop.arrive_pickup_s),
                "planned_pickup_at": _at(day0, stop.pickup_s),
                "planned_drop_at": _at(day0, stop.drop_s),
                "deadhead_s": stop.deadhead_s,
                "deadhead_m": stop.deadhead_m,
                "buffer_s": stop.buffer_s,
                "trip_s": stop.trip_s,
                "trip_m": stop.trip_m,
                "trip_p50_s": node.trip_p50_s,
                "slack_s": stop.slack_s,
                "energy_wh": stop.energy_wh + v.energy_used_wh,
                "trip_source": node.trip_source,
                "deadhead_source": stop.deadhead_source,
                "locked": node.trip_id in locked,
                "unassigned_reason": None,
            }
        )
    route = {
        "driver_id": v.driver_id,
        "vehicle_id": v.vehicle_id,
        "trip_count": seq_offset + len(ev.stops),
        "start_at": _at(day0, ev.start_s) if ev.start_s is not None else None,
        "end_at": _at(day0, ev.end_s) if ev.end_s is not None else None,
        "end_leg_s": ev.end_leg_s,
        "end_leg_m": ev.end_leg_m,
        "energy_wh": ev.energy_wh + v.energy_used_wh,
        "energy_cap_wh": v.energy_cap_wh,
        "soc_fraction": v.soc_fraction,
        "distance_m": ev.distance_m,
    }
    return rows, route


def unassigned_row(trip_id: uuid.UUID, reason: str, node: Any = None, locked: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(ASSIGNMENT_FIELDS)
    row.update(trip_id=trip_id, unassigned_reason=reason, locked=locked)
    if node is not None:
        row.update(
            trip_s=node.service_s,
            trip_m=node.trip_m,
            trip_p50_s=node.trip_p50_s,
            trip_source=node.trip_source,
        )
    return row


def copy_assignment(a: PlanAssignment) -> dict[str, Any]:
    return {f: getattr(a, f) for f in ASSIGNMENT_FIELDS}


def copy_route(r: PlanRoute) -> dict[str, Any]:
    return {f: getattr(r, f) for f in ROUTE_FIELDS}


def kpis(assignments: Sequence[dict[str, Any]] | Sequence[PlanAssignment]) -> dict[str, Any]:
    def g(row: Any, key: str) -> Any:
        return row[key] if isinstance(row, dict) else getattr(row, key)

    assigned = [a for a in assignments if g(a, "driver_id") is not None]
    unassigned = [a for a in assignments if g(a, "driver_id") is None]
    n = len(assigned)
    sources: Counter[str] = Counter()
    for a in assigned:
        if g(a, "trip_source"):
            sources[g(a, "trip_source")] += 1
        if g(a, "deadhead_source"):
            sources[g(a, "deadhead_source")] += 1
    return {
        "trips": len(assignments),
        "assigned": n,
        "unassigned": len(unassigned),
        "drivers_used": len({g(a, "driver_id") for a in assigned}),
        "deadhead_min_per_trip": round(sum(g(a, "deadhead_s") or 0 for a in assigned) / n / 60, 1)
        if n
        else 0.0,
        "buffer_h_per_trip": round(sum(g(a, "buffer_s") or 0 for a in assigned) / n / 3600, 3) if n else 0.0,
        "estimates_by_source": dict(sorted(sources.items())),
        "unassigned_by_reason": dict(sorted(Counter(g(a, "unassigned_reason") for a in unassigned).items())),
    }


async def write_version(session: AsyncSession, draft: PlanDraft) -> Plan:
    # Serialise version numbering per service date.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": zlib.crc32(f"plan:{draft.service_date}".encode())}
    )
    current = (
        await session.execute(select(func.max(Plan.version)).where(Plan.service_date == draft.service_date))
    ).scalar_one()
    stats = dict(draft.solver_stats)
    stats["kpis"] = kpis(draft.assignments)
    plan = Plan(
        plan_id=uuid.uuid4(),
        service_date=draft.service_date,
        version=(current or 0) + 1,
        status="draft",
        parent_plan_id=draft.parent_plan_id,
        snapshot_id=draft.snapshot_id,
        trigger=draft.trigger,
        reason=draft.reason,
        solver_params=draft.solver_params,
        solver_stats=stats,
        created_by=draft.created_by,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    await session.flush()
    if draft.assignments:
        await session.execute(
            insert(PlanAssignment),
            [{"id": uuid.uuid4(), "plan_id": plan.plan_id, **row} for row in draft.assignments],
        )
    if draft.routes:
        await session.execute(
            insert(PlanRoute), [{"id": uuid.uuid4(), "plan_id": plan.plan_id, **row} for row in draft.routes]
        )
    k = stats["kpis"]
    day = draft.service_date.isoformat()
    PLAN_UNASSIGNED.labels(service_date=day).set(k["unassigned"])
    PLAN_DEADHEAD_PER_TRIP.labels(service_date=day).set(k["deadhead_min_per_trip"] * 60)
    PLAN_BUFFER_PER_TRIP.labels(service_date=day).set(k["buffer_h_per_trip"] * 3600)
    if draft.status == "published":
        await _publish_rows(session, plan)
    return plan


async def get_plan(session: AsyncSession, plan_id: uuid.UUID) -> Plan:
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise NotFound(f"plan {plan_id} not found", code="plan_not_found")
    return plan


async def latest_plan(session: AsyncSession, service_date: date) -> Plan | None:
    stmt = select(Plan).where(Plan.service_date == service_date).order_by(Plan.version.desc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def unacknowledged(session: AsyncSession, plan: Plan) -> list[uuid.UUID]:
    rows = (
        await session.execute(
            select(PlanAssignment.trip_id, PlanAssignment.unassigned_reason).where(
                PlanAssignment.plan_id == plan.plan_id, PlanAssignment.driver_id.is_(None)
            )
        )
    ).all()
    if not rows:
        return []
    acks = {
        (r.trip_id, r.reason)
        for r in (
            await session.execute(
                select(UnassignedAck).where(UnassignedAck.service_date == plan.service_date)
            )
        ).scalars()
    }
    return [trip_id for trip_id, reason in rows if (trip_id, reason) not in acks]


async def _publish_rows(session: AsyncSession, plan: Plan) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(Plan)
        .where(
            Plan.service_date == plan.service_date, Plan.status == "published", Plan.plan_id != plan.plan_id
        )
        .values(status="superseded")
    )
    await session.flush()
    plan.status = "published"
    plan.published_at = now
    await session.flush()


async def publish(session: AsyncSession, plan: Plan, *, require_acks: bool = True) -> Plan:
    if plan.status == "published":
        raise Conflict("plan is already published", code="already_published")
    if plan.status == "superseded":
        raise Conflict("a superseded version cannot be published; publish a newer version", code="superseded")
    if require_acks:
        missing = await unacknowledged(session, plan)
        if missing:
            raise Unprocessable(
                f"{len(missing)} unassigned trips need an acknowledged reason before publishing",
                code="unassigned_not_acknowledged",
                trip_ids=[str(t) for t in missing],
            )
    await _publish_rows(session, plan)
    return plan


async def acknowledge(
    session: AsyncSession, plan: Plan, actor: str, trip_ids: Iterable[uuid.UUID] | None, note: str
) -> int:
    rows = (
        await session.execute(
            select(PlanAssignment.trip_id, PlanAssignment.unassigned_reason).where(
                PlanAssignment.plan_id == plan.plan_id, PlanAssignment.driver_id.is_(None)
            )
        )
    ).all()
    wanted = set(trip_ids) if trip_ids is not None else None
    count = 0
    for trip_id, reason in rows:
        if wanted is not None and trip_id not in wanted:
            continue
        stmt = (
            insert(UnassignedAck)
            .values(
                id=uuid.uuid4(),
                service_date=plan.service_date,
                trip_id=trip_id,
                reason=reason,
                plan_id=plan.plan_id,
                actor=actor,
                note=note,
            )
            .on_conflict_do_nothing(constraint="uq_unassigned_ack")
        )
        await session.execute(stmt)
        count += 1
    if wanted is not None:
        unknown = wanted - {r[0] for r in rows}
        if unknown:
            raise Unprocessable(
                "trips are not unassigned in this plan",
                code="not_unassigned",
                trip_ids=sorted(str(t) for t in unknown),
            )
    return count


@dataclass(slots=True)
class TripDiff:
    trip_id: uuid.UUID
    driver_before: uuid.UUID | None
    driver_after: uuid.UUID | None
    pickup_shift_min: float | None
    changed: bool


async def diff(
    session: AsyncSession, plan: Plan, other: Plan
) -> tuple[list[TripDiff], dict[uuid.UUID, dict[str, int]]]:
    """Changes going from `other` (before) to `plan` (after)."""

    def by_trip(rows: Sequence[PlanAssignment]) -> dict[uuid.UUID, PlanAssignment]:
        return {r.trip_id: r for r in rows}

    after = by_trip(
        (await session.execute(select(PlanAssignment).where(PlanAssignment.plan_id == plan.plan_id)))
        .scalars()
        .all()
    )
    before = by_trip(
        (await session.execute(select(PlanAssignment).where(PlanAssignment.plan_id == other.plan_id)))
        .scalars()
        .all()
    )
    out: list[TripDiff] = []
    per_driver: dict[uuid.UUID, dict[str, int]] = {}
    for trip_id in sorted(set(after) | set(before), key=str):
        a, b = after.get(trip_id), before.get(trip_id)
        d_after = a.driver_id if a else None
        d_before = b.driver_id if b else None
        shift = None
        if a and b and a.planned_arrive_pickup_at and b.planned_arrive_pickup_at:
            shift = round((a.planned_arrive_pickup_at - b.planned_arrive_pickup_at).total_seconds() / 60, 1)
        changed = d_after != d_before or bool(shift) or (a is None) != (b is None)
        out.append(TripDiff(trip_id, d_before, d_after, shift, changed))
        if d_after != d_before:
            if d_after is not None:
                per_driver.setdefault(d_after, {"gained": 0, "lost": 0})["gained"] += 1
            if d_before is not None:
                per_driver.setdefault(d_before, {"gained": 0, "lost": 0})["lost"] += 1
    return out, per_driver
