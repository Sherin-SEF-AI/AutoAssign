from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.core.timeutil import IST
from app.db.models import JobRun
from app.db.session import session_scope
from app.worker import SCHEDULE, Worker, next_run_preview

pytestmark = pytest.mark.integration


async def test_abandoned_runs_are_closed_only_without_lock(clean) -> None:  # type: ignore[no-untyped-def]
    async with session_scope(clean.factory) as s:
        s.add(
            JobRun(
                job_name="nightly_solve",
                service_date=date(2026, 10, 6),
                started_at=datetime.now(UTC),
                status="running",
                stats={},
            )
        )
        s.add(JobRun(job_name="calibrate_factors", started_at=datetime.now(UTC), status="running", stats={}))
    await clean.redis.set("lock:job:calibrate_factors", "x", ex=60)
    assert await Worker(clean).mark_abandoned() == 1
    async with clean.factory() as s:
        rows = {r.job_name: r.status for r in (await s.execute(select(JobRun))).scalars()}
    assert rows == {"nightly_solve": "failed", "calibrate_factors": "running"}


def test_schedule_matches_spec() -> None:
    names = [n for n, _ in SCHEDULE]
    assert names == [
        "ingest",
        "nightly_solve",
        "auto_publish",
        "morning_validate",
        "intraday_monitor",
        "eta_actuals",
        "calibrate_factors",
        "purge_here_cache",
    ]
    preview = next_run_preview(datetime(2026, 10, 5, 19, 0, tzinfo=IST))
    assert preview["ingest"].startswith("2026-10-05T19:45:00+05:30")
    assert preview["nightly_solve"].startswith("2026-10-05T20:00:00+05:30")
    assert preview["auto_publish"].startswith("2026-10-05T21:30:00+05:30")
    assert preview["morning_validate"].startswith("2026-10-06T06:00:00+05:30")
    assert preview["purge_here_cache"].startswith("2026-10-06T03:00:00+05:30")
    assert preview["calibrate_factors"].startswith("2026-10-11T02:00:00+05:30")
