"""Plan service: full solves, ops moves with feasibility, locks, publish and notifications."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import Notification, Notifier
from app.config import Settings
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.logging import get_logger
from app.core.metrics import SOLVE_DURATION
from app.db.models import Override, Plan
from app.db.queries import plan_assignments, trips_in_snapshot
from app.eta.estimator import LegEstimator
from app.graph.build import Quantile, build_graph, plannable_trips, trip_nodes, vehicle_contexts
from app.plan.context import PlanContext, load_plan_context
from app.plan.inputs import PlanningInputs
from app.plan.versions import (
    PlanDraft,
    copy_assignment,
    copy_route,
    get_plan,
    publish,
    rows_from_eval,
    unassigned_row,
    write_version,
)
from app.solver.evaluate import Issue, RouteEval
from app.solver.extract import extract
from app.solver.model import SolveOptions, solve

log = get_logger("plan")


def solver_params(
    settings: Settings, opts: SolveOptions, policy: str, quantile: str, snapshot_id: uuid.UUID
) -> dict[str, Any]:
    return {
        **opts.params(settings),
        "buffer_policy": policy,
        "quantile": quantile,
        "snapshot_id": str(snapshot_id),
        "buffer_fixed_s": settings.buffer_fixed_s,
        "buffer_pct": settings.buffer_pct,
        "buffer_min_s": settings.buffer_min_s,
        "range_caps": settings.range_caps_json,
        "e_roll": settings.e_roll_json,
        "max_deadhead_km": settings.max_deadhead_km,
        "here_enabled": settings.here_available,
        "osrm_enabled": settings.osrm_available,
    }


async def solve_inputs(
    session: AsyncSession,
    settings: Settings,
    estimator: LegEstimator,
    inputs: PlanningInputs,
    *,
    created_by: str,
    trigger: str,
    reason: str,
    parent_plan_id: uuid.UUID | None = None,
    quantile: Quantile = "p80",
    use_soc: bool = False,
) -> Plan:
    """Full solve of a snapshot into a new draft version."""
    trips = plannable_trips(inputs.trips)
    nodes = await trip_nodes(inputs.service_date, trips, settings, estimator, quantile)
    vehicles = vehicle_contexts(
        inputs.service_date, inputs.drivers, inputs.vehicles, settings, inputs.soc_pct if use_soc else None
    )
    graph = await build_graph(inputs.service_date, nodes, vehicles, settings, estimator, quantile=quantile)
    opts = SolveOptions(
        time_limit_s=settings.solver_time_limit_s, solution_limit=settings.solver_solution_limit
    )
    started = time.perf_counter()
    result = solve(graph, settings, opts)
    SOLVE_DURATION.labels(mode="full").observe(time.perf_counter() - started)
    ex = extract(graph, settings, result)
    assignments: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for ev in ex.routes.values():
        rows, route = rows_from_eval(graph, ev, locked=set())
        assignments += rows
        routes.append(route)
    for i, why in sorted(ex.unassigned.items()):
        assignments.append(unassigned_row(graph.nodes[i].trip_id, why, graph.nodes[i]))
    draft = PlanDraft(
        service_date=inputs.service_date,
        snapshot_id=inputs.snapshot_id,
        trigger=trigger,
        reason=reason,
        created_by=created_by,
        parent_plan_id=parent_plan_id,
        solver_params=solver_params(settings, opts, graph.buffer_policy, quantile, inputs.snapshot_id),
        solver_stats={
            "graph": graph.stats,
            "estimator": estimator.stats.as_dict(),
            "objective": result.objective,
            "status": result.status,
            "solve_s": result.solve_s,
            "solutions": result.solutions,
            "stop_reason": result.stop_reason,
        },
        assignments=assignments,
        routes=routes,
    )
    return await write_version(session, draft)


# Moves ----------------------------------------------------------------------------------


@dataclass(slots=True)
class DriverPreview:
    driver_id: uuid.UUID | None
    stops: list[dict[str, Any]]
    energy_wh: int
    energy_cap_wh: int
    end_at: Any


@dataclass(slots=True)
class MoveOutcome:
    accepted: bool
    forced: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    drivers: list[DriverPreview]
    plan: Plan | None
    override_id: uuid.UUID | None = None
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _issue_dict(i: Issue, driver_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "code": i.code,
        "severity": i.severity,
        "message": i.message,
        "trip_id": str(i.trip_id) if i.trip_id else None,
        "driver_id": str(driver_id) if driver_id else None,
    }


def _preview(pctx: PlanContext, ev: RouteEval) -> DriverPreview:
    rows, route = rows_from_eval(pctx.graph, ev, locked=set())
    return DriverPreview(
        driver_id=route["driver_id"],
        stops=[
            {
                "trip_id": str(r["trip_id"]),
                "seq": r["seq"],
                "planned_depart_at": r["planned_depart_at"].isoformat(),
                "planned_arrive_pickup_at": r["planned_arrive_pickup_at"].isoformat(),
                "planned_pickup_at": r["planned_pickup_at"].isoformat(),
                "planned_drop_at": r["planned_drop_at"].isoformat(),
                "slack_s": r["slack_s"],
                "deadhead_s": r["deadhead_s"],
                "buffer_s": r["buffer_s"],
            }
            for r in rows
        ],
        energy_wh=route["energy_wh"],
        energy_cap_wh=route["energy_cap_wh"],
        end_at=route["end_at"].isoformat() if route["end_at"] else None,
    )


async def rebuild_version(
    session: AsyncSession,
    pctx: PlanContext,
    new_routes: dict[uuid.UUID, list[int]],
    *,
    trigger: str,
    reason: str,
    actor: str,
    locked: set[uuid.UUID],
    unassigned: dict[uuid.UUID, str] | None = None,
    evals: dict[uuid.UUID, RouteEval] | None = None,
    status: str = "draft",
    extra_stats: dict[str, Any] | None = None,
) -> Plan:
    """New version: drivers not in new_routes are copied verbatim from the parent."""
    changed = set(new_routes)
    moved_trips = {pctx.graph.nodes[i].trip_id for seq in new_routes.values() for i in seq}
    assignments: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    unassigned = dict(unassigned or {})
    for a in pctx.assignments:
        if a.driver_id is not None and a.driver_id not in changed and a.trip_id not in moved_trips:
            row = copy_assignment(a)
            row["locked"] = a.trip_id in locked
            assignments.append(row)
        elif a.driver_id is None and a.trip_id not in moved_trips and a.trip_id not in unassigned:
            row = copy_assignment(a)
            row["locked"] = False
            assignments.append(row)
        elif a.driver_id is not None and a.driver_id in changed and a.trip_id not in moved_trips:
            unassigned.setdefault(a.trip_id, "ops_unassigned")
    for driver_id, route_row in pctx.route_rows.items():
        if driver_id not in changed:
            routes.append(copy_route(route_row))
    vidx = pctx.vehicle_index
    for driver_id, seq in new_routes.items():
        ev = (evals or {}).get(driver_id)
        if ev is None:
            await pctx.book.ensure(vidx[driver_id], seq)
            ev = pctx.book.evaluate(vidx[driver_id], seq)
        rows, route = rows_from_eval(pctx.graph, ev, locked=locked)
        assignments += rows
        if seq:
            routes.append(route)
    idx = pctx.node_index
    for trip_id, why in sorted(unassigned.items(), key=lambda kv: str(kv[0])):
        if any(r["trip_id"] == trip_id for r in assignments):
            continue
        node = pctx.graph.nodes[idx[trip_id]] if trip_id in idx else None
        assignments.append(unassigned_row(trip_id, why, node))
    draft = PlanDraft(
        service_date=pctx.plan.service_date,
        snapshot_id=pctx.plan.snapshot_id,
        trigger=trigger,
        reason=reason,
        created_by=actor,
        parent_plan_id=pctx.plan.plan_id,
        status=status,
        solver_params=dict(pctx.plan.solver_params),
        solver_stats={
            "parent_version": pctx.plan.version,
            "changed_drivers": sorted(str(d) for d in changed),
            **(extra_stats or {}),
        },
        assignments=assignments,
        routes=routes,
    )
    return await write_version(session, draft)


async def move_trip(
    session: AsyncSession,
    settings: Settings,
    estimator: LegEstimator,
    plan_id: uuid.UUID,
    *,
    trip_id: uuid.UUID,
    to_driver_id: uuid.UUID | None,
    reason: str,
    force: bool,
    actor: str,
    dry_run: bool = False,
) -> MoveOutcome:
    plan = await get_plan(session, plan_id)
    if plan.status == "superseded":
        raise Conflict("cannot move trips on a superseded version", code="superseded")
    pctx = await load_plan_context(session, plan, settings, estimator)
    current = pctx.assignment(trip_id)
    if current is None or trip_id not in pctx.node_index:
        raise NotFound(f"trip {trip_id} is not part of plan {plan_id}", code="trip_not_in_plan")
    from_driver = current.driver_id
    if from_driver == to_driver_id:
        raise Unprocessable("the trip is already with that driver", code="no_change")
    vidx = pctx.vehicle_index
    if to_driver_id is not None and to_driver_id not in vidx:
        raise Unprocessable("target driver is not on shift for this date", code="driver_not_available")
    node = pctx.node_index[trip_id]
    new_routes: dict[uuid.UUID, list[int]] = {}
    if from_driver is not None:
        new_routes[from_driver] = [i for i in pctx.seq_of(from_driver) if i != node]
    if to_driver_id is not None:
        seq = [*pctx.seq_of(to_driver_id), node]
        seq.sort(key=lambda i: (pctx.graph.nodes[i].pickup_s, str(pctx.graph.nodes[i].trip_id)))
        new_routes[to_driver_id] = seq
    evals: dict[uuid.UUID, RouteEval] = {}
    for driver_id, seq in new_routes.items():
        if driver_id not in vidx:
            continue
        await pctx.book.ensure(vidx[driver_id], seq)
        evals[driver_id] = pctx.book.evaluate(vidx[driver_id], seq)
    errors = [_issue_dict(i, d) for d, ev in evals.items() for i in ev.errors]
    warnings = [_issue_dict(i, d) for d, ev in evals.items() for i in ev.warnings]
    previews = [_preview(pctx, ev) for ev in evals.values()]
    feasibility = {"errors": errors, "warnings": warnings}
    if dry_run:
        return MoveOutcome(False, False, errors, warnings, previews, None, message="dry run")
    if errors and not force:
        ov = Override(
            plan_id_before=plan.plan_id,
            plan_id_after=None,
            service_date=plan.service_date,
            actor=actor,
            trip_id=trip_id,
            from_driver_id=from_driver,
            to_driver_id=to_driver_id,
            reason=reason,
            feasibility=feasibility,
            forced=False,
            accepted=False,
        )
        session.add(ov)
        await session.flush()
        return MoveOutcome(
            False,
            False,
            errors,
            warnings,
            previews,
            None,
            ov.override_id,
            "hard constraint violations; resend with force and a reason to accept",
        )
    if errors and force and not reason.strip():
        raise Unprocessable("a forced move needs a reason", code="reason_required")
    locked = {a.trip_id for a in pctx.assignments if a.locked}
    unassigned = {trip_id: "ops_unassigned"} if to_driver_id is None else None
    new_plan = await rebuild_version(
        session,
        pctx,
        {d: s for d, s in new_routes.items() if d in vidx},
        trigger="override",
        reason=reason or "ops move",
        actor=actor,
        locked=locked,
        unassigned=unassigned,
        evals=evals,
    )
    ov = Override(
        plan_id_before=plan.plan_id,
        plan_id_after=new_plan.plan_id,
        service_date=plan.service_date,
        actor=actor,
        trip_id=trip_id,
        from_driver_id=from_driver,
        to_driver_id=to_driver_id,
        reason=reason,
        feasibility=feasibility,
        forced=bool(errors),
        accepted=True,
    )
    session.add(ov)
    await session.flush()
    return MoveOutcome(True, bool(errors), errors, warnings, previews, new_plan, ov.override_id)


async def set_lock(
    session: AsyncSession,
    settings: Settings,
    estimator: LegEstimator,
    plan_id: uuid.UUID,
    trip_id: uuid.UUID,
    *,
    locked: bool,
    actor: str,
) -> Plan:
    plan = await get_plan(session, plan_id)
    if plan.status == "superseded":
        raise Conflict("cannot change locks on a superseded version", code="superseded")
    rows = await plan_assignments(session, plan.plan_id)
    target = next((a for a in rows if a.trip_id == trip_id), None)
    if target is None:
        raise NotFound(f"trip {trip_id} is not part of plan {plan_id}", code="trip_not_in_plan")
    if target.driver_id is None:
        raise Unprocessable("only assigned trips can be locked", code="not_assigned")
    if target.locked == locked:
        raise Conflict("assignment already " + ("locked" if locked else "unlocked"), code="no_change")
    pctx = await load_plan_context(session, plan, settings, estimator)
    lock_set = {a.trip_id for a in rows if a.locked}
    if locked:
        lock_set.add(trip_id)
    else:
        lock_set.discard(trip_id)
    new_plan = await rebuild_version(
        session,
        pctx,
        {},
        trigger="lock" if locked else "unlock",
        reason=f"{'lock' if locked else 'unlock'} {trip_id}",
        actor=actor,
        locked=lock_set,
    )
    session.add(
        Override(
            plan_id_before=plan.plan_id,
            plan_id_after=new_plan.plan_id,
            service_date=plan.service_date,
            actor=actor,
            action="lock" if locked else "unlock",
            trip_id=trip_id,
            from_driver_id=target.driver_id,
            to_driver_id=target.driver_id,
            reason="",
            feasibility={},
            forced=False,
            accepted=True,
        )
    )
    return new_plan


async def driver_payload(session: AsyncSession, plan: Plan) -> list[dict[str, Any]]:
    """Per driver ordered sequence with pickup times and addresses, nothing else."""
    trips = {t.trip_id: t for t in await trips_in_snapshot(session, plan.snapshot_id)}
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for a in await plan_assignments(session, plan.plan_id):
        if a.driver_id is None or a.seq is None:
            continue
        t = trips.get(a.trip_id)
        out.setdefault(a.driver_id, []).append(
            {
                "seq": a.seq,
                "trip_id": str(a.trip_id),
                "pickup_at": a.planned_pickup_at.isoformat() if a.planned_pickup_at else None,
                "pickup_address": t.pickup_address if t else "",
                "drop_address": t.drop_address if t else "",
            }
        )
    return [
        {"driver_id": str(d), "stops": sorted(stops, key=lambda s: s["seq"])}
        for d, stops in sorted(out.items(), key=lambda kv: str(kv[0]))
    ]


async def publish_and_notify(
    session: AsyncSession, notifier: Notifier, plan: Plan, *, require_acks: bool = True
) -> Plan:
    await publish(session, plan, require_acks=require_acks)
    payload = await driver_payload(session, plan)
    await session.commit()
    await notifier.send(
        Notification(
            event="plan.published",
            service_date=plan.service_date,
            plan_id=plan.plan_id,
            data={"version": plan.version, "drivers": payload},
        )
    )
    return plan


async def notify_plan(notifier: Notifier, event: str, plan: Plan, **data: Any) -> None:
    await notifier.send(
        Notification(
            event=event,
            service_date=plan.service_date,
            plan_id=plan.plan_id,
            data={
                "version": plan.version,
                "trigger": plan.trigger,
                "kpis": plan.solver_stats.get("kpis", {}),
                **data,
            },
        )
    )
