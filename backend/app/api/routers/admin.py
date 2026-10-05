"""Admin: synthetic dataset regeneration, snapshots, provider budgets."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.adapters.base import Regenerable
from app.adapters.here.budget import day_tag
from app.api.deps import Admin, Ctx, EffectiveSettings, Session, User
from app.api.schemas import SnapshotOut
from app.core.errors import Conflict
from app.core.events import publish
from app.db.models import JobRun, LegEstimate
from app.db.queries import list_snapshots
from app.eta.factory import breaker, budget_guard
from app.jobs.runner import JobRunHandle, create_run, run_job
from app.jobs.seed import after_dataset_load

router = APIRouter(tags=["admin"])


class RegenerateIn(BaseModel):
    seed: int = Field(default=42, ge=0, le=2**31)
    days_past: int = Field(default=45, ge=0, le=120)
    days_future: int = Field(default=7, ge=0, le=30)


class RegenerateAccepted(BaseModel):
    run_id: uuid.UUID


@router.post("/admin/synthetic/regenerate", response_model=RegenerateAccepted, status_code=202)
async def regenerate(body: RegenerateIn, request: Request, ctx: Ctx, _: Admin) -> RegenerateAccepted:
    source = ctx.source
    if not isinstance(source, Regenerable):
        raise Conflict("the configured data source cannot be regenerated", code="not_regenerable")

    async def job(h: JobRunHandle) -> dict[str, Any]:
        async def progress(done: int, total: int, day: date) -> None:
            await h.progress({"done": done, "total": total, "day": day.isoformat()})

        settings = await ctx.settings()
        result = await source.regenerate(
            ctx.factory,
            settings,
            seed=body.seed,
            days_past=body.days_past,
            days_future=body.days_future,
            today=await ctx.today(),
            progress=progress,
        )
        result["post"] = await after_dataset_load(ctx)
        await publish(ctx.redis, "data.regenerated", {"seed": body.seed})
        return result

    run_id = await create_run(ctx, "synthetic_regenerate", None, status="queued")
    task = asyncio.create_task(run_job(ctx, "synthetic_regenerate", job, lock_ttl_s=3600, run_id=run_id))
    request.app.state.tasks.add(task)
    task.add_done_callback(request.app.state.tasks.discard)
    return RegenerateAccepted(run_id=run_id)


@router.get("/snapshots", response_model=list[SnapshotOut])
async def snapshots(
    session: Session, _: User, date_: Annotated[date | None, Query(alias="date")] = None
) -> list[SnapshotOut]:
    return [SnapshotOut.model_validate(s) for s in await list_snapshots(session, date_)]


class BudgetRow(BaseModel):
    provider: str
    used: int
    cap: int
    remaining: int
    circuit: str


class BudgetOut(BaseModel):
    day: str
    here_enabled: bool
    osrm_enabled: bool
    providers: list[BudgetRow]


@router.get("/admin/budget", response_model=BudgetOut)
async def budget(ctx: Ctx, settings: EffectiveSettings, _: User) -> BudgetOut:
    now = datetime.now(UTC)
    snap = await budget_guard(settings, ctx.redis).snapshot(now)
    rows = []
    for provider, data in snap.items():
        circuit_name = "here_matrix" if provider.startswith("here_matrix") else provider
        state = await breaker(settings, ctx.redis, circuit_name).state()
        rows.append(BudgetRow(provider=provider, circuit=state, **data))
    return BudgetOut(
        day=day_tag(now),
        here_enabled=settings.here_available,
        osrm_enabled=settings.osrm_available,
        providers=rows,
    )


class EstimateSourceRow(BaseModel):
    kind: str
    source: str
    count: int


class EstimatesOut(BaseModel):
    service_date: date
    rows: list[EstimateSourceRow]
    last_run: dict[str, Any] | None


@router.get("/admin/estimates", response_model=EstimatesOut)
async def estimates(session: Session, _: User, date_: Annotated[date, Query(alias="date")]) -> EstimatesOut:
    """Estimates by source for a service date: stored rows, plus the latest estimate or solve run."""
    stmt = (
        select(LegEstimate.kind, LegEstimate.source, func.count())
        .where(LegEstimate.service_date == date_)
        .group_by(LegEstimate.kind, LegEstimate.source)
        .order_by(LegEstimate.kind, LegEstimate.source)
    )
    rows = [
        EstimateSourceRow(kind=k, source=s, count=int(n)) for k, s, n in (await session.execute(stmt)).all()
    ]
    run = (
        await session.execute(
            select(JobRun)
            .where(
                JobRun.service_date == date_,
                JobRun.job_name.in_(("estimate_day", "nightly_solve", "solve_date")),
            )
            .where(JobRun.status == "succeeded")
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    last = (
        {"job_name": run.job_name, "started_at": run.started_at.isoformat(), "stats": run.stats}
        if run
        else None
    )
    return EstimatesOut(service_date=date_, rows=rows, last_run=last)
