"""Incremental repair: re-solve only the freed drivers, keep everyone else byte identical.

Trips already under way (pickup time passed, or a live status beyond assigned) are frozen on
their driver and never repaired; the driver becomes available after the last frozen trip.
The current plan seeds OR-Tools through ReadAssignmentFromRoutes, untouched drivers are
locked with ApplyLocksToAllVehicles. If the seed cannot be read (for example a forced override
left a locked route infeasible) the model is rebuilt over the freed drivers only, which gives
the same guarantee.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.geo import Point
from app.core.logging import get_logger
from app.core.metrics import REPAIR_EVENTS, SOLVE_DURATION
from app.core.timeutil import day_start_utc, seconds_since_day_start
from app.db.models import Plan, PlanAssignment, RepairEvent
from app.db.queries import plan_assignments, plan_routes, trip_live_map
from app.eta.estimator import LegEstimator
from app.graph.build import build_graph, plannable_trips, trip_nodes, vehicle_contexts
from app.graph.model import Edge, Graph, HubLeg, VehicleCtx
from app.plan.context import apply_stored_trip_legs, stored_overrides
from app.plan.inputs import load_inputs
from app.plan.versions import (
    PlanDraft,
    copy_assignment,
    copy_route,
    rows_from_eval,
    unassigned_row,
    write_version,
)
from app.solver.extract import extract, unassigned_reason
from app.solver.model import SolveError, SolveOptions, solve

log = get_logger("repair")

IN_PROGRESS = ("arrived", "started", "completed", "no_show")
TRIGGERS = ("soc_shortfall", "absent_driver", "late_predicted", "late_booking", "cancellation", "manual")


@dataclass(slots=True)
class RepairRequest:
    trigger: str
    reason: str
    actor: str
    free_drivers: set[uuid.UUID] = field(default_factory=set)
    absent_drivers: set[uuid.UUID] = field(default_factory=set)
    soc_pct: dict[uuid.UUID, float] = field(default_factory=dict)  # vehicle -> SOC percent
    new_snapshot_id: uuid.UUID | None = None
    now: datetime | None = None
    trip_ids: list[uuid.UUID] = field(default_factory=list)
    available: dict[uuid.UUID, tuple[int, Point]] = field(default_factory=dict)  # driver -> (s, where)
    include_float: bool = True
    must_keep: set[uuid.UUID] = field(default_factory=set)  # never trade these for unassigned
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RepairOutcome:
    outcome: str  # repaired, no_feasible_repair, no_change
    plan: Plan | None
    event: RepairEvent
    freed: list[uuid.UUID]
    unassigned: list[uuid.UUID]


def float_drivers(
    assignments: Iterable[PlanAssignment],
    vehicles: Iterable[VehicleCtx],
    ref_s: int,
    day0: datetime,
    horizon_s: int,
) -> set[uuid.UUID]:
    """Drivers on shift at ref_s with no assignment starting in the next horizon_s."""
    busy: set[uuid.UUID] = set()
    for a in assignments:
        if a.driver_id is None or a.planned_depart_at is None or a.planned_drop_at is None:
            continue
        start = int((a.planned_depart_at - day0).total_seconds())
        end = int((a.planned_drop_at - day0).total_seconds())
        if start < ref_s + horizon_s and end > ref_s:
            busy.add(a.driver_id)
    return {
        v.driver_id for v in vehicles if v.shift_start_s <= ref_s < v.shift_end_s and v.driver_id not in busy
    }


def _subgraph(
    graph: Graph, keep_nodes: list[int], keep_vehicles: list[int]
) -> tuple[Graph, list[int], list[int]]:
    nmap = {old: new for new, old in enumerate(keep_nodes)}
    vmap = {old: new for new, old in enumerate(keep_vehicles)}
    sub = Graph(
        service_date=graph.service_date,
        nodes=[graph.nodes[i] for i in keep_nodes],
        vehicles=[graph.vehicles[v] for v in keep_vehicles],
        buffer_policy=graph.buffer_policy,
        stats=dict(graph.stats),
    )
    sub.edges = {(nmap[i], nmap[j]): e for (i, j), e in graph.edges.items() if i in nmap and j in nmap}
    sub.start_legs = {
        (vmap[v], nmap[j]): leg for (v, j), leg in graph.start_legs.items() if v in vmap and j in nmap
    }
    sub.end_legs = {
        (nmap[i], vmap[v]): leg for (i, v), leg in graph.end_legs.items() if v in vmap and i in nmap
    }
    return sub, keep_nodes, keep_vehicles


async def repair(
    session: AsyncSession,
    settings: Settings,
    estimator: LegEstimator,
    plan: Plan,
    req: RepairRequest,
) -> RepairOutcome:
    started = time.perf_counter()
    d = plan.service_date
    snapshot_id = req.new_snapshot_id or plan.snapshot_id
    inputs = await load_inputs(session, d, snapshot_id=snapshot_id)
    assignments = list(await plan_assignments(session, plan.plan_id))
    route_rows = {r.driver_id: r for r in await plan_routes(session, plan.plan_id)}
    live = await trip_live_map(session, d)
    now_s = seconds_since_day_start(req.now, d) if req.now is not None else None
    trips_by_id = {t.trip_id: t for t in inputs.trips}

    # Frozen prefixes per driver.
    by_driver: dict[uuid.UUID, list[PlanAssignment]] = {}
    for a in assignments:
        if a.driver_id is not None and a.seq is not None:
            by_driver.setdefault(a.driver_id, []).append(a)
    for rows in by_driver.values():
        rows.sort(key=lambda r: r.seq or 0)

    def is_frozen(a: PlanAssignment) -> bool:
        lv = live.get(a.trip_id)
        if lv is not None and lv.status in IN_PROGRESS:
            return True
        # A pickup whose time has passed cannot be handed to anyone else.
        if now_s is not None and a.planned_pickup_at is not None:
            return seconds_since_day_start(a.planned_pickup_at, d) <= now_s
        return False

    frozen: dict[uuid.UUID, list[PlanAssignment]] = {}
    for driver_id, rows in by_driver.items():
        prefix: list[PlanAssignment] = []
        for r in rows:
            if is_frozen(r) and driver_id not in req.absent_drivers:
                prefix.append(r)
            else:
                break
        frozen[driver_id] = prefix
    frozen_trips = {r.trip_id for rows in frozen.values() for r in rows}

    # Vehicles: snapshot roster, SOC from the parent plan unless overridden, minus absent drivers.
    soc = {r.vehicle_id: r.soc_fraction * 100 for r in route_rows.values()}
    soc.update(req.soc_pct)
    base = [
        v
        for v in vehicle_contexts(d, inputs.drivers, inputs.vehicles, settings, soc)
        if v.driver_id not in req.absent_drivers
    ]

    # Who may change: the trigger's drivers plus float drivers. Everyone else keeps their plan.
    free = set(req.free_drivers)
    if req.include_float:
        if now_s is not None:
            ref = now_s
        else:
            pickups = [
                seconds_since_day_start(trips_by_id[t].scheduled_pickup_at, d)
                for t in req.trip_ids
                if t in trips_by_id
            ]
            ref = min(pickups, default=6 * 3600)
        free |= float_drivers(assignments, base, ref, day_start_utc(d), settings.float_horizon_s)

    vehicles: list[VehicleCtx] = []
    for v in base:
        prefix = frozen.get(v.driver_id, [])
        live_state = v.driver_id in free
        if live_state and v.driver_id in req.available:
            at_s, where = req.available[v.driver_id]
            used = (prefix[-1].energy_wh or 0) if prefix else 0
            v = replace(v, available_from_s=at_s, available_at=where, energy_used_wh=int(used))
        elif prefix:
            last = prefix[-1]
            t = trips_by_id.get(last.trip_id)
            drop_s = (
                seconds_since_day_start(last.planned_drop_at, d) if last.planned_drop_at else v.shift_start_s
            )
            lv = live.get(last.trip_id)
            if live_state and lv is not None and lv.actual_drop_at is not None:
                drop_s = seconds_since_day_start(lv.actual_drop_at, d)
            where = Point(t.drop_lat, t.drop_lng) if t else v.start
            v = replace(
                v, available_from_s=drop_s, available_at=where, energy_used_wh=int(last.energy_wh or 0)
            )
        elif live_state and now_s is not None and now_s > v.shift_start_s:
            v = replace(v, available_from_s=now_s)
        vehicles.append(v)

    # Nodes: plannable trips not frozen. Trips on the plan stay plannable regardless of status words.
    model_trips = [t for t in plannable_trips(inputs.trips) if t.trip_id not in frozen_trips]
    nodes = apply_stored_trip_legs(await trip_nodes(d, model_trips, settings, estimator), assignments)
    edges, starts, ends = stored_overrides(assignments, route_rows)
    # A driver with a frozen prefix starts from its last frozen drop: the stored deadhead into the
    # first non frozen trip is exactly that leg.
    start_over: dict[tuple[uuid.UUID, uuid.UUID], HubLeg] = dict(starts)
    for driver_id, prefix in frozen.items():
        rest = by_driver[driver_id][len(prefix) :]
        if prefix and rest:
            r = rest[0]
            dh = r.deadhead_s or 0
            start_over[(driver_id, r.trip_id)] = HubLeg(
                dh, dh, dh, r.deadhead_m or 0, r.deadhead_source or "stored", r.buffer_s or 0
            )
    edge_over: dict[tuple[uuid.UUID, uuid.UUID], Edge] = {
        k: v for k, v in edges.items() if k[0] not in frozen_trips and k[1] not in frozen_trips
    }
    graph = await build_graph(
        d,
        nodes,
        vehicles,
        settings,
        estimator,
        edge_overrides=edge_over,
        start_overrides=start_over,
        end_overrides=ends,
        policy=str(plan.solver_params.get("buffer_policy") or "fixed"),
    )
    idx, vidx = graph.node_index(), graph.vehicle_index()

    free &= set(vidx)
    locked_v = frozenset(vidx[drv] for drv in vidx if drv not in free)
    initial: dict[int, list[int]] = {}
    for drv, rows in by_driver.items():
        if drv not in vidx:
            continue
        initial[vidx[drv]] = [idx[r.trip_id] for r in rows[len(frozen.get(drv, [])) :] if r.trip_id in idx]
    pinned = {
        idx[a.trip_id]: vidx[a.driver_id]
        for a in assignments
        if a.locked and a.driver_id in free and a.trip_id in idx and a.driver_id in vidx
    }
    opts = SolveOptions(
        time_limit_s=settings.incremental_time_limit_s,
        solution_limit=settings.incremental_solution_limit,
        mode="incremental",
        initial_routes=initial,
        locked_vehicles=locked_v,
        pinned=pinned,
    )
    model_used = "locks"
    solve_started = time.perf_counter()
    try:
        result = solve(graph, settings, opts)
        sub_nodes = list(range(len(graph.nodes)))
        sub_vehicles = list(range(len(graph.vehicles)))
        solved_graph = graph
    except SolveError as exc:
        log.warning("repair_seed_failed", error=str(exc), plan_id=str(plan.plan_id))
        locked_trip_nodes = {i for v in locked_v for i in initial.get(v, [])}
        keep_nodes = [i for i in range(len(graph.nodes)) if i not in locked_trip_nodes]
        keep_vehicles = sorted(set(range(len(graph.vehicles))) - set(locked_v))
        solved_graph, sub_nodes, sub_vehicles = _subgraph(graph, keep_nodes, keep_vehicles)
        nmap = {old: new for new, old in enumerate(sub_nodes)}
        vmap = {old: new for new, old in enumerate(sub_vehicles)}
        sub_opts = SolveOptions(
            time_limit_s=settings.incremental_time_limit_s,
            solution_limit=settings.incremental_solution_limit,
            mode="incremental",
            initial_routes={
                vmap[v]: [nmap[i] for i in seq if i in nmap] for v, seq in initial.items() if v in vmap
            },
            pinned={nmap[i]: vmap[v] for i, v in pinned.items() if i in nmap and v in vmap},
        )
        model_used = "free_drivers_only"
        try:
            result = solve(solved_graph, settings, sub_opts)
        except SolveError as exc2:
            return await _no_repair(session, plan, req, f"{exc}; {exc2}", started)
    SOLVE_DURATION.labels(mode="incremental").observe(time.perf_counter() - solve_started)
    ex = extract(solved_graph, settings, result)

    # Assemble the new version.
    free_ids = {graph.vehicles[v].driver_id for v in range(len(graph.vehicles)) if v not in locked_v}
    present_trips = {t.trip_id for t in inputs.trips if t.status != "cancelled"}
    rows_out: list[dict[str, Any]] = []
    routes_out: list[dict[str, Any]] = []
    locked_ids = {a.trip_id for a in assignments if a.locked}
    for drv, rows in by_driver.items():
        if drv in free_ids or drv in req.absent_drivers:
            continue
        rows_out += [copy_assignment(r) for r in rows if r.trip_id in present_trips]
        if drv in route_rows:
            routes_out.append(copy_route(route_rows[drv]))
    for sub_v, ev in ex.routes.items():
        v_idx = sub_vehicles[sub_v]
        drv = graph.vehicles[v_idx].driver_id
        if drv not in free_ids:
            continue
        prefix = frozen.get(drv, [])
        rows_out += [copy_assignment(r) for r in prefix]
        new_rows, route = rows_from_eval(solved_graph, ev, locked=locked_ids, seq_offset=len(prefix))
        rows_out += new_rows
        if prefix or new_rows:
            if prefix and not new_rows and drv in route_rows:
                routes_out.append(copy_route(route_rows[drv]))
            else:
                if prefix:
                    route["start_at"] = prefix[0].planned_depart_at
                routes_out.append(route)
    assigned_now = {r["trip_id"] for r in rows_out}
    unassigned: list[uuid.UUID] = []
    for sub_i, why in sorted(ex.unassigned.items()):
        trip_id = solved_graph.nodes[sub_i].trip_id
        if trip_id in assigned_now:
            continue
        rows_out.append(unassigned_row(trip_id, why, solved_graph.nodes[sub_i]))
        unassigned.append(trip_id)
        assigned_now.add(trip_id)
    # Any plannable trip still missing (absent driver with no new home and not in the model) is
    # reported as unassigned rather than silently dropped.
    for t in model_trips:
        if t.trip_id not in assigned_now:
            i = idx[t.trip_id]
            rows_out.append(unassigned_row(t.trip_id, unassigned_reason(graph, settings, i), graph.nodes[i]))
            unassigned.append(t.trip_id)
            assigned_now.add(t.trip_id)
    lost = req.must_keep & set(unassigned)
    if lost:
        return await _no_repair(
            session, plan, req, f"no driver can take {len(lost)} trips in time; they stay as planned", started
        )
    status = "published" if plan.status == "published" else "draft"
    moved = _moved(assignments, rows_out)
    draft = PlanDraft(
        service_date=d,
        snapshot_id=snapshot_id,
        trigger=req.trigger,
        reason=req.reason,
        created_by=req.actor,
        parent_plan_id=plan.plan_id,
        status=status,
        solver_params={**plan.solver_params, **opts.params(settings), "model": model_used},
        solver_stats={
            "parent_version": plan.version,
            "graph": graph.stats,
            "objective": result.objective,
            "solve_s": result.solve_s,
            "stop_reason": result.stop_reason,
            "freed_drivers": sorted(str(x) for x in free_ids),
            "frozen_trips": len(frozen_trips),
            "moved_trips": [str(t) for t in moved],
        },
        assignments=rows_out,
        routes=routes_out,
    )
    new_plan = await write_version(session, draft)
    event = RepairEvent(
        service_date=d,
        trigger=req.trigger,
        outcome="repaired",
        plan_id_before=plan.plan_id,
        plan_id_after=new_plan.plan_id,
        trip_ids=list(dict.fromkeys([*req.trip_ids, *moved])),
        details={
            **req.details,
            "reason": req.reason,
            "freed_drivers": sorted(str(x) for x in free_ids),
            "model": model_used,
            "unassigned": [str(t) for t in unassigned],
            "moved_trips": [str(t) for t in moved],
            "duration_s": round(time.perf_counter() - started, 2),
        },
    )
    session.add(event)
    await session.flush()
    REPAIR_EVENTS.labels(trigger=req.trigger).inc()
    return RepairOutcome("repaired", new_plan, event, sorted(free_ids), unassigned)


def _moved(before: Iterable[PlanAssignment], after: Iterable[Mapping[str, Any]]) -> list[uuid.UUID]:
    prev = {a.trip_id: a.driver_id for a in before}
    return sorted(
        (r["trip_id"] for r in after if prev.get(r["trip_id"], "absent") != r["driver_id"]), key=str
    )


async def _no_repair(
    session: AsyncSession, plan: Plan, req: RepairRequest, error: str, started: float
) -> RepairOutcome:
    event = RepairEvent(
        service_date=plan.service_date,
        trigger=req.trigger,
        outcome="no_feasible_repair",
        plan_id_before=plan.plan_id,
        plan_id_after=None,
        trip_ids=list(req.trip_ids),
        details={
            **req.details,
            "reason": req.reason,
            "error": error,
            "duration_s": round(time.perf_counter() - started, 2),
        },
    )
    session.add(event)
    await session.flush()
    REPAIR_EVENTS.labels(trigger=req.trigger).inc()
    return RepairOutcome("no_feasible_repair", None, event, [], [])
