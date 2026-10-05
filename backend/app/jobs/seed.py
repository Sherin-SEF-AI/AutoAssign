"""Steps that run after a dataset is (re)loaded so history driven features work on day one."""

from __future__ import annotations

from typing import Any

from app.jobs.context import AppContext


async def after_dataset_load(ctx: AppContext) -> dict[str, Any]:
    return {}
