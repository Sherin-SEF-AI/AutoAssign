"""Ingest and full solve for a service date, used by POST /plans/solve, make solve and nightly_solve."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.db.models import Plan
from app.db.session import session_scope
from app.eta.factory import build_estimator
from app.jobs.context import AppContext
from app.jobs.ingest import ingest_date
from app.plan.inputs import load_inputs
from app.plan.service import notify_plan, solve_inputs
from app.plan.versions import latest_plan


async def solve_date(
    ctx: AppContext,
    service_date: date,
    *,
    created_by: str,
    trigger: str = "on_demand",
    reason: str = "",
    skip_if_unchanged: bool = False,
) -> tuple[Plan | None, dict[str, Any]]:
    ingest = await ingest_date(ctx, service_date)
    settings = await ctx.settings()
    async with session_scope(ctx.factory) as session:
        previous = await latest_plan(session, service_date)
        if skip_if_unchanged and previous is not None and str(previous.snapshot_id) == ingest["snapshot_id"]:
            return None, {
                "skipped": True,
                "reason": "snapshot unchanged",
                "plan_id": str(previous.plan_id),
                "snapshot_id": ingest["snapshot_id"],
            }
        inputs = await load_inputs(session, service_date)
        estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http)
        plan = await solve_inputs(
            session,
            settings,
            estimator,
            inputs,
            created_by=created_by,
            trigger=trigger,
            reason=reason or f"{trigger} solve",
            parent_plan_id=previous.plan_id if previous else None,
        )
    await notify_plan(ctx.notifier, "plan.drafted", plan)
    stats = {
        "plan_id": str(plan.plan_id),
        "version": plan.version,
        "snapshot_id": str(plan.snapshot_id),
        "ingest": ingest,
        **plan.solver_stats,
    }
    return plan, stats
