"""Runs an incremental repair for a job and sends the notifications."""

from __future__ import annotations

from typing import Any

from app.adapters.base import Notification
from app.db.models import Plan
from app.db.session import session_scope
from app.eta.factory import build_estimator
from app.jobs.context import AppContext
from app.plan.repair import RepairOutcome, RepairRequest, repair
from app.plan.service import driver_payload, notify_plan


async def run_repair(ctx: AppContext, plan: Plan, req: RepairRequest) -> RepairOutcome:
    settings = await ctx.settings()
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http)
    async with session_scope(ctx.factory) as session:
        outcome = await repair(session, settings, estimator, plan, req)
        payload = (
            await driver_payload(session, outcome.plan)
            if outcome.plan and outcome.plan.status == "published"
            else None
        )
    extra: dict[str, Any] = {
        "repair_trigger": req.trigger,
        "outcome": outcome.outcome,
        "trip_ids": [str(t) for t in outcome.event.trip_ids],
    }
    if outcome.plan is not None:
        await notify_plan(ctx.notifier, "plan.repaired", outcome.plan, **extra)
        if payload is not None:
            await ctx.notifier.send(
                Notification(
                    event="plan.published",
                    service_date=outcome.plan.service_date,
                    plan_id=outcome.plan.plan_id,
                    data={"version": outcome.plan.version, "drivers": payload},
                )
            )
    else:
        await ctx.notifier.send(
            Notification(
                event="plan.repaired", service_date=plan.service_date, plan_id=plan.plan_id, data=extra
            )
        )
    return outcome
