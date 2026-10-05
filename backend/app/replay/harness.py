"""Replay harness: the go or no-go evidence for the real rollout.

For each past day: rebuild the snapshot from the DataSource, plan every trip that was booked
(cancelled excluded) with P90 durations in place of P80, and report drivers used, unassigned
trips, deadhead and buffer per trip, solve time and estimate sources. Each chain is then
replayed against the recorded actual trip durations to count pickups that would have been late.
Estimates are offline (cache, OSRM, fallback) so a replay never spends HERE budget.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.adapters.base import TripRecord
from app.config import Settings
from app.core.logging import get_logger
from app.db.models import ReplayRun
from app.db.session import session_scope
from app.eta.estimator import LegEstimator
from app.eta.factory import build_estimator
from app.graph.build import build_graph, trip_nodes, vehicle_contexts
from app.jobs.context import AppContext
from app.jobs.ingest import ingest_date
from app.plan.inputs import load_inputs
from app.solver.extract import extract
from app.solver.model import SolveOptions, solve

log = get_logger("replay")


@dataclass(slots=True)
class DayResult:
    service_date: str
    trips: int
    assigned: int
    unassigned: int
    unassigned_by_reason: dict[str, int]
    drivers_available: int
    drivers_used: int
    deadhead_min_per_trip: float
    buffer_h_per_trip: float
    solve_s: float
    stop_reason: str
    estimate_sources: dict[str, int]
    infeasible_chains: int
    actual_checked: int
    actual_late_pickups: int
    assigned_share: float = 0.0
    errors: list[str] = field(default_factory=list)


async def replay_day(
    ctx: AppContext,
    settings: Settings,
    estimator: LegEstimator,
    service_date: date,
    *,
    solution_limit: int,
    time_limit_s: int,
) -> DayResult:
    await ingest_date(ctx, service_date)
    async with ctx.factory() as session:
        inputs = await load_inputs(session, service_date)
    booked: list[TripRecord] = sorted(
        (t.model_copy(update={"status": "booked"}) for t in inputs.trips if t.status != "cancelled"),
        key=lambda t: str(t.trip_id),
    )
    actual = {
        t.trip_id: (t.actual_drop_at - t.actual_pickup_at).total_seconds()
        for t in inputs.trips
        if t.actual_pickup_at and t.actual_drop_at
    }
    nodes = await trip_nodes(service_date, booked, settings, estimator, quantile="p90")
    vehicles = vehicle_contexts(service_date, inputs.drivers, inputs.vehicles, settings)
    graph = await build_graph(service_date, nodes, vehicles, settings, estimator, quantile="p90")
    started = time.perf_counter()
    result = solve(graph, settings, SolveOptions(time_limit_s=time_limit_s, solution_limit=solution_limit))
    solve_s = time.perf_counter() - started
    ex = extract(graph, settings, result)
    assigned = sum(len(ev.stops) for ev in ex.routes.values())
    dh = sum(s.deadhead_s for ev in ex.routes.values() for s in ev.stops)
    buf = sum(s.buffer_s for ev in ex.routes.values() for s in ev.stops)
    infeasible = [ev for ev in ex.routes.values() if not ev.feasible]
    checked = late = 0
    for ev in ex.routes.values():
        free: float | None = None
        for k, stop in enumerate(ev.stops):
            node = graph.nodes[stop.node]
            dh_p50 = stop.deadhead_s
            if k > 0:
                e = graph.edges.get((ev.stops[k - 1].node, stop.node))
                dh_p50 = e.deadhead_p50_s if e else stop.deadhead_s
            if free is not None:
                checked += 1
                if free + dh_p50 > stop.pickup_s + settings.late_tolerance_s:
                    late += 1
            duration = actual.get(node.trip_id, node.trip_p50_s)
            free = max(free + dh_p50 if free is not None else stop.pickup_s, stop.pickup_s) + duration
    used = sum(1 for ev in ex.routes.values() if ev.stops)
    return DayResult(
        service_date=service_date.isoformat(),
        trips=len(nodes),
        assigned=assigned,
        unassigned=len(ex.unassigned),
        unassigned_by_reason=dict(sorted(Counter(ex.unassigned.values()).items())),
        drivers_available=len(vehicles),
        drivers_used=used,
        deadhead_min_per_trip=round(dh / assigned / 60, 1) if assigned else 0.0,
        buffer_h_per_trip=round(buf / assigned / 3600, 3) if assigned else 0.0,
        solve_s=round(solve_s, 2),
        stop_reason=result.stop_reason,
        estimate_sources=dict(sorted(graph.sources.items())),
        infeasible_chains=len(infeasible),
        actual_checked=checked,
        actual_late_pickups=late,
        assigned_share=round(assigned / len(nodes), 3) if nodes else 1.0,
        errors=[f"vehicle {ev.vehicle}: {i.code}" for ev in infeasible for i in ev.errors],
    )


def summarise(days: list[DayResult]) -> dict[str, Any]:
    if not days:
        return {"days": 0}
    n = len(days)
    checked = sum(d.actual_checked for d in days)
    reasons: Counter[str] = Counter()
    for d in days:
        reasons.update(d.unassigned_by_reason)
    return {
        "days": n,
        "trips": sum(d.trips for d in days),
        "assigned_share_mean": round(sum(d.assigned_share for d in days) / n, 3),
        "assigned_share_min": min(d.assigned_share for d in days),
        "drivers_used_mean": round(sum(d.drivers_used for d in days) / n, 1),
        "drivers_used_max": max(d.drivers_used for d in days),
        "deadhead_min_per_trip_mean": round(sum(d.deadhead_min_per_trip for d in days) / n, 1),
        "buffer_h_per_trip_mean": round(sum(d.buffer_h_per_trip for d in days) / n, 3),
        "solve_s_mean": round(sum(d.solve_s for d in days) / n, 2),
        "solve_s_max": max(d.solve_s for d in days),
        "infeasible_chains": sum(d.infeasible_chains for d in days),
        "unassigned_by_reason": dict(sorted(reasons.items())),
        "actual_late_rate": round(sum(d.actual_late_pickups for d in days) / checked, 4) if checked else None,
        "every_drop_has_reason": all(d.unassigned == sum(d.unassigned_by_reason.values()) for d in days),
        "time_limit_stops": sum(1 for d in days if d.stop_reason == "time_limit"),
    }


def render_markdown(
    run_at: datetime, params: dict[str, Any], days: list[DayResult], summary: dict[str, Any]
) -> str:
    lines = [
        f"# Replay report {run_at.date().isoformat()}",
        "",
        "Go or no-go evidence for the rollout. Every past synthetic day is re-planned from its",
        "snapshot with P90 durations in place of P80. Chains are then replayed against the recorded",
        "actual trip durations (deadheads at P50) to count pickups later than LATE_TOLERANCE_S.",
        "",
        f"Parameters: `{params}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for k, v in summary.items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## Days",
        "",
        "| Date | Trips | Assigned | Share | Unassigned (reasons) | Drivers used / available "
        "| Deadhead min/trip | Buffer h/trip | Solve s | Stop | Sources | Infeasible chains "
        "| Late vs actuals |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in days:
        reasons = ", ".join(f"{k} {v}" for k, v in d.unassigned_by_reason.items()) or "none"
        sources = ", ".join(f"{k} {v}" for k, v in d.estimate_sources.items())
        lines.append(
            f"| {d.service_date} | {d.trips} | {d.assigned} | {d.assigned_share:.0%} "
            f"| {d.unassigned} ({reasons}) | "
            f"{d.drivers_used} / {d.drivers_available} | {d.deadhead_min_per_trip} | {d.buffer_h_per_trip} | "
            f"{d.solve_s} | {d.stop_reason} | {sources} | {d.infeasible_chains} | "
            f"{d.actual_late_pickups} of {d.actual_checked} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_report(target: Path, started: datetime, text: str) -> str:
    target.mkdir(parents=True, exist_ok=True)
    file = target / f"replay-{started.date().isoformat()}.md"
    file.write_text(text)
    return str(file)


async def run_replay(
    ctx: AppContext,
    *,
    days: int,
    out_dir: str | None,
    solution_limit: int | None = None,
    time_limit_s: int | None = None,
    end: date | None = None,
    run_id: uuid.UUID | None = None,
) -> tuple[list[DayResult], dict[str, Any], str | None]:
    settings = await ctx.settings()
    today = end or await ctx.today()
    sol_limit = solution_limit or settings.solver_solution_limit
    t_limit = time_limit_s or settings.solver_time_limit_s
    params: dict[str, Any] = {
        "days": days,
        "quantile": "p90",
        "solution_limit": sol_limit,
        "time_limit_s": t_limit,
        "data_source": ctx.source.name,
    }
    started = datetime.now(UTC)
    rid = run_id or uuid.uuid4()
    async with session_scope(ctx.factory) as session:
        session.add(
            ReplayRun(id=rid, started_at=started, status="running", params=params, results=[], summary={})
        )
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http, use_here=False)
    results: list[DayResult] = []
    for offset in range(days, 0, -1):
        d = today - timedelta(days=offset)
        try:
            day = await replay_day(
                ctx,
                settings,
                estimator,
                d,
                solution_limit=sol_limit,
                time_limit_s=t_limit,
            )
        except Exception as exc:
            log.error("replay_day_failed", service_date=d.isoformat(), error=str(exc))
            async with session_scope(ctx.factory) as session:
                row = await session.get(ReplayRun, rid)
                if row is not None:
                    row.status = "failed"
                    row.finished_at = datetime.now(UTC)
                    row.summary = {"error": f"{d.isoformat()}: {exc}"}
            raise
        results.append(day)
        log.info("replay_day", **{k: v for k, v in asdict(day).items() if k != "errors"})
        async with session_scope(ctx.factory) as session:
            row = await session.get(ReplayRun, rid)
            if row is not None:
                row.results = [asdict(r) for r in results]
    summary = summarise(results)
    path: str | None = None
    if out_dir:
        path = await asyncio.to_thread(
            _write_report, Path(out_dir), started, render_markdown(started, params, results, summary)
        )
    async with session_scope(ctx.factory) as session:
        row = await session.get(ReplayRun, rid)
        if row is not None:
            row.status = "succeeded"
            row.finished_at = datetime.now(UTC)
            row.summary = summary
            row.report_path = path
    return results, summary, path


async def run_replay_cli(ctx: AppContext, *, days: int, out_dir: str) -> int:
    _, summary, path = await run_replay(ctx, days=days, out_dir=out_dir)
    print(f"replay report: {path}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0 if summary.get("infeasible_chains", 1) == 0 else 1
