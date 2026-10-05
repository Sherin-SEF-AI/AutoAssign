"""Replay of 45 synthetic past days with P90: zero infeasible chains and a written report."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.adapters.synthetic.writer import generate_dataset
from app.jobs.seed import after_dataset_load
from app.replay.harness import run_replay

pytestmark = [pytest.mark.integration, pytest.mark.replay]


async def test_replay_45_days(clean, settings, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    today = await clean.today()
    await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=45, days_future=0)
    await after_dataset_load(clean)
    days, summary, path = await run_replay(
        clean, days=45, out_dir=str(tmp_path), solution_limit=15, time_limit_s=20
    )
    assert len(days) == 45
    assert summary["infeasible_chains"] == 0
    assert summary["every_drop_has_reason"] is True
    assert all(d.unassigned == sum(d.unassigned_by_reason.values()) for d in days)
    assert path is not None
    text = await asyncio.to_thread(Path(path).read_text)
    assert "## Summary" in text and text.count("\n| 20") == 45
    assert summary["drivers_used_max"] <= 27
