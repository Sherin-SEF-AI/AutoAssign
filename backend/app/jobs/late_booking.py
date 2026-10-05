"""late_booking: incremental re-solve on trip.created, trip.updated and trip.cancelled events."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from redis.asyncio import Redis

from app.core.geo import haversine_m
from app.core.logging import get_logger
from app.core.timeutil import service_date_of
from app.db.queries import plan_assignments
from app.db.session import session_scope
from app.eta.factory import build_estimator
from app.jobs.context import AppContext
from app.jobs.repairs import run_repair
from app.plan.context import load_plan_context
from app.plan.events import EventResult
from app.plan.inputs import load_inputs
from app.plan.repair import RepairRequest
from app.plan.versions import latest_plan

log = get_logger("jobs.late_booking")
QUEUE = "queue:trip_events"
CANDIDATES = 4


async def enqueue(redis: Redis, result: EventResult) -> None:
    await redis.rpush(
        QUEUE,
        json.dumps(
            {
                "event_type": result.event_type,
                "trip_id": str(result.trip_id),
                "service_date": result.service_date,
                "snapshot_id": str(result.snapshot_id) if result.snapshot_id else None,
            }
        ),
    )


async def late_booking(
    ctx: AppContext, *, event_type: str, trip_id: uuid.UUID, service_date: date, snapshot_id: uuid.UUID | None
) -> dict[str, Any]:
    now = await ctx.clock.now()
    async with ctx.factory() as session:
        plan = await latest_plan(session, service_date)
    if plan is None:
        return {"skipped": True, "reason": "no plan for the date yet; the nightly solve will include it"}
    if snapshot_id is not None and plan.snapshot_id == snapshot_id:
        return {"skipped": True, "reason": "plan already built on this snapshot"}
    trigger = "cancellation" if event_type == "trip.cancelled" else "late_booking"
    free: set[uuid.UUID] = set()
    include_float = True
    if trigger == "cancellation":
        include_float = False
        async with ctx.factory() as session:
            for a in await plan_assignments(session, plan.plan_id):
                if a.trip_id == trip_id and a.driver_id is not None:
                    free.add(a.driver_id)
    else:
        free = await _candidate_drivers(ctx, plan, trip_id, snapshot_id)
    req = RepairRequest(
        trigger=trigger,
        reason=f"{event_type} {trip_id}",
        actor=f"job:{trigger}",
        free_drivers=free,
        new_snapshot_id=snapshot_id,
        # Intraday the trips already under way are frozen; the night before nothing is.
        now=now if service_date_of(now) == service_date else None,
        trip_ids=[trip_id],
        include_float=include_float,
        details={"event_type": event_type},
    )
    outcome = await run_repair(ctx, plan, req)
    return {
        "trigger": trigger,
        "outcome": outcome.outcome,
        "plan_id": str(outcome.plan.plan_id) if outcome.plan else None,
        "freed": [str(d) for d in outcome.freed],
        "unassigned": [str(t) for t in outcome.unassigned],
    }


async def _candidate_drivers(
    ctx: AppContext, plan: Any, trip_id: uuid.UUID, snapshot_id: uuid.UUID | None
) -> set[uuid.UUID]:
    """Eligible drivers that can take the new trip in their current sequence, closest first."""
    settings = await ctx.settings()
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http)
    async with session_scope(ctx.factory) as session:
        inputs = await load_inputs(session, plan.service_date, snapshot_id=snapshot_id or plan.snapshot_id)
        pctx = await load_plan_context(session, plan, settings, estimator, inputs=inputs)
    idx = pctx.node_index
    if trip_id not in idx:
        return set()
    node_i = idx[trip_id]
    node = pctx.graph.nodes[node_i]
    ranked: list[tuple[int, float, str, uuid.UUID]] = []
    for v_idx, v in enumerate(pctx.graph.vehicles):
        if not v.eligible(node) or not (v.shift_start_s <= node.pickup_s <= v.shift_end_s):
            continue
        seq = [i for i in pctx.seq_of(v.driver_id) if i != node_i]
        trial = sorted([*seq, node_i], key=lambda i: pctx.graph.nodes[i].pickup_s)
        await pctx.book.ensure(v_idx, trial)
        feasible = pctx.book.evaluate(v_idx, trial).feasible
        before = [i for i in trial if pctx.graph.nodes[i].pickup_s < node.pickup_s]
        anchor = pctx.graph.nodes[before[-1]].drop if before else v.start
        ranked.append((0 if feasible else 1, haversine_m(anchor, node.pickup), str(v.driver_id), v.driver_id))
    ranked.sort()
    return {r[3] for r in ranked[:CANDIDATES]}


async def drain_queue(ctx: AppContext, *, block_s: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Process queued trip events. block_s > 0 waits for the first one (worker loop)."""
    out: list[dict[str, Any]] = []
    for _ in range(limit):
        if block_s > 0 and not out:
            item = await ctx.redis.blpop([QUEUE], timeout=block_s)
            raw: bytes | str | None = item[1] if item else None
        else:
            popped = await ctx.redis.lpop(QUEUE)
            raw = popped if isinstance(popped, bytes | str) else None
        if raw is None:
            break
        data = json.loads(raw)
        try:
            stats = await late_booking(
                ctx,
                event_type=data["event_type"],
                trip_id=uuid.UUID(data["trip_id"]),
                service_date=date.fromisoformat(data["service_date"]),
                snapshot_id=uuid.UUID(data["snapshot_id"]) if data.get("snapshot_id") else None,
            )
        except Exception as exc:
            log.exception("late_booking_failed", error=str(exc), trip_event=data)
            stats = {"error": str(exc)}
        out.append({**data, **stats})
    return out
