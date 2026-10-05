"""Admin: synthetic dataset regeneration, snapshots, provider budgets."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.adapters.base import Regenerable
from app.api.deps import Admin, Ctx, Session, User
from app.api.schemas import SnapshotOut
from app.core.errors import Conflict
from app.core.events import publish
from app.db.queries import list_snapshots
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
