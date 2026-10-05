"""nightly_solve (20:00 IST) and auto_publish (21:30 IST)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.timeutil import IST
from app.db.models import Override
from app.db.session import session_scope
from app.jobs.context import AppContext
from app.jobs.solve import solve_date
from app.plan.service import publish_and_notify
from app.plan.versions import acknowledge, latest_plan, unacknowledged

AUTO_ACTOR = "job:auto_publish"


async def nightly_solve(ctx: AppContext, service_date: date) -> dict[str, Any]:
    """Idempotent: a rerun on an unchanged snapshot is a no-op."""
    _, stats = await solve_date(
        ctx, service_date, created_by="job:nightly_solve", trigger="nightly", skip_if_unchanged=True
    )
    return stats


async def auto_publish(ctx: AppContext, service_date: date) -> dict[str, Any]:
    """Publish the latest draft unless ops touched the date since the 20:00 solve."""
    cutoff = datetime.combine(service_date - timedelta(days=1), time(20, 0), tzinfo=IST)
    async with session_scope(ctx.factory) as session:
        plan = await latest_plan(session, service_date)
        if plan is None:
            return {"skipped": True, "reason": "no plan"}
        if plan.status != "draft":
            return {
                "skipped": True,
                "reason": f"latest version is {plan.status}",
                "plan_id": str(plan.plan_id),
            }
        touched = (
            await session.execute(
                select(func.count())
                .select_from(Override)
                .where(
                    Override.service_date == service_date,
                    Override.created_at >= cutoff,
                    Override.accepted.is_(True),
                )
            )
        ).scalar_one()
        if touched:
            return {
                "skipped": True,
                "reason": f"{touched} ops overrides since 20:00; ops publishes",
                "plan_id": str(plan.plan_id),
            }
        missing = await unacknowledged(session, plan)
        if missing:
            # Drivers need a plan tonight; unassigned trips are acknowledged on ops' behalf and stay visible.
            await acknowledge(session, plan, AUTO_ACTOR, missing, "auto acknowledged at auto publish")
        await publish_and_notify(session, ctx.notifier, plan)
        return {
            "plan_id": str(plan.plan_id),
            "version": plan.version,
            "auto_acknowledged": len(missing),
        }
