"""Job history and manual triggers."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import Ctx, Session, User
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import JobRunOut
from app.core.errors import NotFound
from app.db.models import JobRun
from app.jobs.registry import REGISTRY, resolve_date
from app.jobs.runner import create_run, run_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobInfo(BaseModel):
    name: str
    description: str
    default_date: str


@router.get("", response_model=list[JobInfo])
async def list_jobs(_: User) -> list[JobInfo]:
    return [
        JobInfo(name=s.name, description=s.description, default_date=s.default_date)
        for s in REGISTRY.values()
    ]


@router.get("/runs", response_model=Page[JobRunOut])
async def list_runs(
    session: Session,
    _: User,
    page: Annotated[PageParams, Depends(page_params)],
    name: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[JobRunOut]:
    stmt = select(JobRun).order_by(JobRun.started_at.desc()).limit(page.offset + page.limit + 1)
    if name:
        stmt = stmt.where(JobRun.job_name == name)
    rows = [JobRunOut.model_validate(r) for r in (await session.execute(stmt)).scalars()]
    return paginate(rows, page)


@router.get("/runs/{run_id}", response_model=JobRunOut)
async def get_run(run_id: uuid.UUID, session: Session, _: User) -> JobRunOut:
    row = await session.get(JobRun, run_id)
    if row is None:
        raise NotFound(f"job run {run_id} not found", code="job_run_not_found")
    return JobRunOut.model_validate(row)


class RunIn(BaseModel):
    service_date: date | None = None


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    job_name: str
    service_date: date | None


@router.post("/{name}/run", response_model=RunAccepted, status_code=202)
async def trigger(name: str, request: Request, ctx: Ctx, _: User, body: RunIn | None = None) -> RunAccepted:
    spec = REGISTRY.get(name)
    if spec is None:
        raise NotFound(f"unknown job {name}", code="job_not_found")
    day = resolve_date(spec, await ctx.today(), body.service_date if body else None)
    run_id = await create_run(ctx, name, day, status="queued")
    task = asyncio.create_task(
        run_job(ctx, name, spec.fn, service_date=day, lock_ttl_s=spec.lock_ttl_s, run_id=run_id)
    )
    tasks: set[asyncio.Task[object]] = request.app.state.tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return RunAccepted(run_id=run_id, job_name=name, service_date=day)
