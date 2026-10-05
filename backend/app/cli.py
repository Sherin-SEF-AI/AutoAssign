"""Command line entrypoints used by the Makefile: seed, ingest, solve, replay, run-job."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from typing import Any

from app.adapters.base import Regenerable
from app.config import get_settings
from app.core.logging import configure_logging
from app.jobs.context import AppContext
from app.jobs.registry import REGISTRY, resolve_date
from app.jobs.runner import run_job
from app.jobs.seed import after_dataset_load
from app.replay.harness import run_replay_cli
from app.runtime import close_context, create_context


def _print(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, default=str))


async def _seed(ctx: AppContext, args: argparse.Namespace) -> int:
    source = ctx.source
    if not isinstance(source, Regenerable):
        print("seed requires DATA_SOURCE=synthetic", file=sys.stderr)
        return 2

    async def progress(done: int, total: int, day: date) -> None:
        if done == total or done % 10 == 0:
            print(f"  wrote {done}/{total} days (last {day})", flush=True)

    result = await source.regenerate(
        ctx.factory,
        await ctx.settings(),
        seed=args.seed,
        days_past=args.days_past,
        days_future=args.days_future,
        today=await ctx.today(),
        progress=progress,
    )
    result["post"] = await after_dataset_load(ctx)
    _print(result)
    return 0


async def _run_job(ctx: AppContext, args: argparse.Namespace) -> int:
    spec = REGISTRY.get(args.name)
    if spec is None:
        print(f"unknown job {args.name}; known: {', '.join(sorted(REGISTRY))}", file=sys.stderr)
        return 2
    explicit = date.fromisoformat(args.date) if args.date else None
    day = resolve_date(spec, await ctx.today(), explicit)
    outcome = await run_job(ctx, spec.name, spec.fn, service_date=day, lock_ttl_s=spec.lock_ttl_s)
    _print({"job": spec.name, "status": outcome.status, "stats": outcome.stats, "error": outcome.error})
    return 0 if outcome.status in ("succeeded", "skipped") else 1


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dispatch")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed", help="generate the synthetic dataset")
    p_seed.add_argument("--seed", type=int, default=42)
    p_seed.add_argument("--days-past", type=int, default=45)
    p_seed.add_argument("--days-future", type=int, default=7)
    p_job = sub.add_parser("run-job", help="run a named job once")
    p_job.add_argument("name")
    p_job.add_argument("--date", default=None)
    p_solve = sub.add_parser("solve", help="ingest and solve a service date")
    p_solve.add_argument("--date", default=None)
    p_replay = sub.add_parser("replay", help="replay harness over past days")
    p_replay.add_argument("--days", type=int, default=45)
    p_replay.add_argument("--out", default="../docs")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    ctx = await create_context(settings, pool_size=5)
    try:
        if args.cmd == "seed":
            return await _seed(ctx, args)
        if args.cmd == "run-job":
            return await _run_job(ctx, args)
        if args.cmd == "solve":
            day = args.date or (await ctx.today() + timedelta(days=1)).isoformat()
            ns = argparse.Namespace(name="solve_date", date=day)
            return await _run_job(ctx, ns)
        if args.cmd == "replay":
            return await run_replay_cli(ctx, days=args.days, out_dir=args.out)
        return 2
    finally:
        await close_context(ctx)


def main() -> None:
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
