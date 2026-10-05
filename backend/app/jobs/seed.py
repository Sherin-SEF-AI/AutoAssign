"""Steps that run after a dataset is (re)loaded so history driven features work on day one."""

from __future__ import annotations

from typing import Any

from app.jobs.actuals import dates_with_snapshots, eta_actuals
from app.jobs.calibrate import calibrate_factors
from app.jobs.context import AppContext
from app.jobs.purge import ensure_ping_partitions


async def after_dataset_load(ctx: AppContext) -> dict[str, Any]:
    """Backfill eta_log from historical actuals, then calibrate the factor table."""
    today = await ctx.today()
    past = await dates_with_snapshots(ctx, before=today)
    actuals = await eta_actuals(ctx, past)
    calibration = await calibrate_factors(ctx)
    partitions = await ensure_ping_partitions(ctx, today)
    return {"eta_actuals": actuals, "calibration": calibration, "ping_partitions": partitions}
