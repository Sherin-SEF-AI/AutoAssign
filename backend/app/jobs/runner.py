"""Job execution wrapper: Redis lock, job_run row, metrics, structured logging."""

from __future__ import annotations

import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy import update

from app.core.events import publish
from app.core.locks import LockBusy, redis_lock
from app.core.logging import get_logger
from app.core.metrics import JOB_RUNS
from app.db.models import JobRun
from app.db.session import session_scope
from app.jobs.context import AppContext

log = get_logger("jobs")

JobFn = Callable[["JobRunHandle"], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class JobRunHandle:
    ctx: AppContext
    name: str
    run_id: uuid.UUID
    service_date: date | None
    params: dict[str, Any] = field(default_factory=dict)

    async def progress(self, stats: dict[str, Any]) -> None:
        async with session_scope(self.ctx.factory) as session:
            await session.execute(update(JobRun).where(JobRun.id == self.run_id).values(stats=stats))


@dataclass(slots=True)
class JobOutcome:
    run_id: uuid.UUID | None
    status: str
    stats: dict[str, Any]
    error: str | None = None


async def create_run(
    ctx: AppContext, name: str, service_date: date | None, status: str = "running"
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with session_scope(ctx.factory) as session:
        session.add(
            JobRun(
                id=run_id,
                job_name=name,
                service_date=service_date,
                started_at=datetime.now(UTC),
                status=status,
                stats={},
            )
        )
    return run_id


async def run_job(
    ctx: AppContext,
    name: str,
    fn: JobFn,
    *,
    service_date: date | None = None,
    params: dict[str, Any] | None = None,
    lock_ttl_s: int = 900,
    run_id: uuid.UUID | None = None,
) -> JobOutcome:
    """Run a job under its lock and record exactly one job_run row."""
    lock_name = f"{name}:{service_date}" if service_date else name
    try:
        async with redis_lock(ctx.redis, lock_name, lock_ttl_s):
            rid = run_id or await create_run(ctx, name, service_date)
            if run_id is not None:
                async with session_scope(ctx.factory) as session:
                    await session.execute(update(JobRun).where(JobRun.id == rid).values(status="running"))
            handle = JobRunHandle(ctx, name, rid, service_date, dict(params or {}))
            with structlog.contextvars.bound_contextvars(job_name=name, job_run_id=str(rid)):
                log.info("job_started", service_date=str(service_date) if service_date else None)
                try:
                    stats = await fn(handle)
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    log.error("job_failed", error=err, trace=traceback.format_exc(limit=8))
                    await _finish(ctx, rid, "failed", {}, err)
                    JOB_RUNS.labels(name=name, status="failed").inc()
                    return JobOutcome(rid, "failed", {}, err)
                status = "skipped" if stats.get("skipped") else "succeeded"
                await _finish(ctx, rid, status, stats, None)
                JOB_RUNS.labels(name=name, status=status).inc()
                log.info("job_finished", status=status, stats=stats)
                return JobOutcome(rid, status, stats)
    except LockBusy:
        JOB_RUNS.labels(name=name, status="locked").inc()
        log.warning("job_locked", job_name=name, service_date=str(service_date) if service_date else None)
        if run_id is not None:
            await _finish(ctx, run_id, "skipped", {"skipped": True, "reason": "locked"}, None)
        return JobOutcome(run_id, "locked", {"skipped": True, "reason": "locked"})


async def _finish(
    ctx: AppContext, run_id: uuid.UUID, status: str, stats: dict[str, Any], error: str | None
) -> None:
    async with session_scope(ctx.factory) as session:
        await session.execute(
            update(JobRun)
            .where(JobRun.id == run_id)
            .values(status=status, stats=stats, error=error, finished_at=datetime.now(UTC))
        )
    try:
        await publish(ctx.redis, "job.finished", {"run_id": str(run_id), "status": status})
    except Exception as exc:
        log.warning("job_event_publish_failed", error=str(exc))
