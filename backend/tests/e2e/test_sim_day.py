"""End-to-end simulated day with every disturbance on.

The engine is stepped directly on a fixed clock (the sim process does the same against wall
time scaled by SIM_SPEED), with the worker's jobs run at their simulated times.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.adapters.synthetic.writer import generate_dataset
from app.core.clock import FixedClock
from app.core.timeutil import ist_at
from app.db.models import EtaLog, MonitorState, RepairEvent
from app.db.session import session_scope
from app.db.settings_store import save_overrides
from app.jobs.actuals import eta_actuals
from app.jobs.late_booking import drain_queue
from app.jobs.monitor import intraday_monitor
from app.jobs.morning import morning_validate
from app.jobs.publishing import auto_publish
from app.jobs.seed import after_dataset_load
from app.jobs.solve import solve_date
from app.simulator.engine import SimEngine, all_disturbances

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


async def test_simulated_day(clean, settings) -> None:  # type: ignore[no-untyped-def]
    ctx = clean
    today = await ctx.today()
    await generate_dataset(ctx.factory, settings, seed=42, today=today, days_past=10, days_future=1)
    await after_dataset_load(ctx)
    async with session_scope(ctx.factory) as s:
        await save_overrides(
            s,
            ctx.base_settings,
            {"solver_solution_limit": 40, "incremental_solution_limit": 15, "incremental_time_limit_s": 10},
            "test",
        )
    ctx.settings_cache.invalidate()
    day = today + timedelta(days=1)
    wall_clock = ctx.clock
    clock = FixedClock(ist_at(day - timedelta(days=1), 20, 0))
    ctx.clock = clock
    try:
        plan, _ = await solve_date(ctx, day, created_by="test", trigger="nightly")
        assert plan is not None
        clock.set(ist_at(day - timedelta(days=1), 21, 30))
        published = await auto_publish(ctx, day)
        assert published.get("plan_id") == str(plan.plan_id)

        engine = SimEngine(ctx, day, disturbances=all_disturbances(True))
        await engine.prepare()
        monitor_runs: list[dict] = []
        t = engine.now
        end = ist_at(day, 23, 45)
        last_hour = -1
        while t < end:
            t = t + timedelta(minutes=5)
            await engine.step_to(t)
            clock.set(t)
            await drain_queue(ctx)
            local_minutes = (t - ist_at(day, 0)).total_seconds() / 60
            if local_minutes == 6 * 60:
                await morning_validate(ctx, day)
            if 6 * 60 <= local_minutes <= 23 * 60 + 30:
                monitor_runs.append(await intraday_monitor(ctx))
            if int(local_minutes // 60) != last_hour:
                last_hour = int(local_minutes // 60)
                await eta_actuals(ctx, [day])
        await eta_actuals(ctx, [day])
    finally:
        ctx.clock = wall_clock

    async with ctx.factory() as s:
        events = (await s.execute(select(RepairEvent).where(RepairEvent.service_date == day))).scalars().all()
        logged = (
            await s.execute(select(func.count()).select_from(EtaLog).where(EtaLog.service_date == day))
        ).scalar_one()
        states = (await s.execute(select(func.count()).select_from(MonitorState))).scalar_one()
    triggers = {e.trigger for e in events}
    summary = {e.trigger: [x.outcome for x in events if x.trigger == e.trigger] for e in events}
    print("repairs", summary, "sim", engine.stats.as_dict())
    for needed in ("soc_shortfall", "absent_driver", "late_booking", "cancellation", "late_predicted"):
        assert needed in triggers, f"missing {needed} repair; got {summary}"
    # No trip predicted late beyond tolerance without a repair event or an explicit no_feasible_repair.
    covered = {str(t) for e in events if e.trigger == "late_predicted" for t in e.trip_ids}
    for run in monitor_runs:
        for r in run.get("repairs", []):
            assert r["trip_id"] in covered
    assert logged > 20, "eta_log populated with actuals"
    cov = (await eta_actuals(ctx, [day]))["p80_coverage"]
    assert cov is not None and 0 < cov <= 1
    assert states > 0
    assert engine.stats.completed > 20 and engine.stats.pings > 1000
