"""eta_actuals: join actual pickup and drop timestamps to stored estimates, write eta_log."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.config import Settings
from app.core.geo import Point
from app.core.metrics import ETA_P80_COVERAGE
from app.core.speed import time_bin_index
from app.core.timeutil import depart_bin, weekday_type
from app.db.models import EtaLog, LegEstimate, Snapshot
from app.db.queries import trip_live_map, trips_in_snapshot
from app.db.session import session_scope
from app.db.snapshots import latest_snapshot
from app.eta.calibration import nearest
from app.eta.factory import build_estimator
from app.eta.types import LegRequest, cache_key
from app.jobs.context import AppContext


async def eta_actuals(ctx: AppContext, dates: Sequence[date] | None = None) -> dict[str, Any]:
    settings = await ctx.settings()
    if dates is None:
        today = await ctx.today()
        dates = [today - timedelta(days=2), today - timedelta(days=1), today]
    # Offline estimator: missing predictions are backfilled without spending provider budget.
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http, use_here=False)
    written = 0
    per_date: dict[str, int] = {}
    for d in dates:
        n = await _one_date(ctx, settings, d, estimator)
        per_date[d.isoformat()] = n
        written += n
    coverage = await p80_coverage(ctx, days=14)
    return {"written": written, "per_date": per_date, "p80_coverage": coverage}


async def _one_date(ctx: AppContext, settings: Settings, d: date, estimator: Any) -> int:
    async with ctx.factory() as session:
        snap = await latest_snapshot(session, d)
        if snap is None:
            return 0
        trips = await trips_in_snapshot(session, snap.snapshot_id)
        live = await trip_live_map(session, d)
        logged = set(
            (await session.execute(select(EtaLog.trip_id).where(EtaLog.service_date == d))).scalars()
        )
    rows: list[tuple[Any, float, datetime, datetime]] = []
    for t in trips:
        if t.trip_type == "package" or t.trip_id in logged:
            continue
        lv = live.get(t.trip_id)
        status = lv.status if lv else t.status
        pickup = (lv.actual_pickup_at if lv else None) or t.actual_pickup_at
        drop = (lv.actual_drop_at if lv else None) or t.actual_drop_at
        if status != "completed" or pickup is None or drop is None or drop <= pickup:
            continue
        rows.append((t, (drop - pickup).total_seconds(), pickup, drop))
    if not rows:
        return 0
    reqs = [
        LegRequest(Point(t.pickup_lat, t.pickup_lng), Point(t.drop_lat, t.drop_lng), t.scheduled_pickup_at)
        for t, *_ in rows
    ]
    keys = [cache_key("trip", r) for r in reqs]
    async with ctx.factory() as session:
        stored = {
            (r.origin_h3, r.dest_h3, r.depart_bin, r.service_date): r
            for r in (
                await session.execute(
                    select(LegEstimate).where(
                        LegEstimate.kind == "trip",
                        tuple_(
                            LegEstimate.origin_h3,
                            LegEstimate.dest_h3,
                            LegEstimate.depart_bin,
                            LegEstimate.service_date,
                        ).in_([(k.origin_h3, k.dest_h3, k.depart_bin, k.service_date) for k in keys]),
                    )
                )
            ).scalars()
        }
    missing = [
        i for i, k in enumerate(keys) if (k.origin_h3, k.dest_h3, k.depart_bin, k.service_date) not in stored
    ]
    backfilled = await estimator.estimate_many([reqs[i] for i in missing], "trip") if missing else []
    filled = dict(zip(missing, backfilled, strict=True))
    centers = [p for _, p in estimator.factors.centroids]
    now = datetime.now(UTC)
    values = []
    for i, (t, actual_s, _pickup, _drop) in enumerate(rows):
        k = keys[i]
        row = stored.get((k.origin_h3, k.dest_h3, k.depart_bin, k.service_date))
        if row is not None:
            p50, p80, source = row.p50_s, row.p80_s, row.source
            base = float((row.provider_payload or {}).get("base_s") or 0.0) or p50
        else:
            e = filled[i]
            p50, p80, source, base = e.p50_s, e.p80_s, e.source, e.base_s or e.p50_s
        origin = Point(t.pickup_lat, t.pickup_lng)
        cluster = nearest(centers, origin)
        values.append(
            {
                "trip_id": t.trip_id,
                "service_date": d,
                "leg_kind": "trip",
                "source": source,
                "predicted_p50_s": p50,
                "predicted_p80_s": p80,
                "base_s": base,
                "actual_s": actual_s,
                "residual_ratio": actual_s / base if base > 0 else 1.0,
                "covered_p80": actual_s <= p80,
                "depart_bin": depart_bin(t.scheduled_pickup_at),
                "time_bin": time_bin_index(settings.speed_profile_json, t.scheduled_pickup_at),
                "weekday_type": weekday_type(d),
                "zone_cluster": cluster,
                "origin_lat": t.pickup_lat,
                "origin_lng": t.pickup_lng,
                "computed_at": now,
            }
        )
    async with session_scope(ctx.factory) as session:
        for i in range(0, len(values), 500):
            stmt = (
                insert(EtaLog)
                .values(values[i : i + 500])
                .on_conflict_do_nothing(constraint="uq_eta_log_trip_leg")
            )
            await session.execute(stmt)
    return len(values)


async def p80_coverage(ctx: AppContext, days: int = 14) -> float | None:
    since = (await ctx.today()) - timedelta(days=days)
    async with ctx.factory() as session:
        row = (
            await session.execute(
                select(func.count(), func.count().filter(EtaLog.covered_p80.is_(True))).where(
                    EtaLog.service_date >= since, EtaLog.covered_p80.is_not(None)
                )
            )
        ).one()
    total, covered = int(row[0]), int(row[1])
    if total == 0:
        return None
    value = round(covered / total, 4)
    ETA_P80_COVERAGE.set(value)
    return value


async def dates_with_snapshots(ctx: AppContext, before: date) -> list[date]:
    async with ctx.factory() as session:
        rows = (
            await session.execute(
                select(Snapshot.service_date)
                .where(Snapshot.service_date < before)
                .distinct()
                .order_by(Snapshot.service_date)
            )
        ).scalars()
        return list(rows)
