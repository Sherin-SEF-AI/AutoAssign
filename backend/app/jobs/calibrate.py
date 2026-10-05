"""calibrate_factors: rebuild zone clusters and the factor table from eta_log."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import h3
from sqlalchemy import delete, select, update

from app.core.geo import Point
from app.db.models import EtaLog, FactorRow, ZoneCluster
from app.db.session import session_scope
from app.eta.calibration import Residual, basis_of, calibrate, kmeans, nearest
from app.jobs.actuals import p80_coverage
from app.jobs.context import AppContext

HISTORY_DAYS = 90
KEEP_CALIBRATIONS = 3
CLUSTER_RES = 7


async def calibrate_factors(ctx: AppContext) -> dict[str, Any]:
    settings = await ctx.settings()
    since = (await ctx.today()) - timedelta(days=HISTORY_DAYS)
    async with ctx.factory() as session:
        logs = list(
            (
                await session.execute(
                    select(
                        EtaLog.id,
                        EtaLog.source,
                        EtaLog.time_bin,
                        EtaLog.weekday_type,
                        EtaLog.residual_ratio,
                        EtaLog.origin_lat,
                        EtaLog.origin_lng,
                    )
                    .where(EtaLog.service_date >= since)
                    .order_by(EtaLog.id)
                )
            ).all()
        )
    if not logs:
        return {"skipped": True, "reason": "no eta_log rows"}
    # Zone clusters: k-means over pickup cell centroids (H3 res 7), weighted by trip count.
    cells: Counter[str] = Counter(h3.latlng_to_cell(r.origin_lat, r.origin_lng, CLUSTER_RES) for r in logs)
    points = [(Point(*h3.cell_to_latlng(c)), n) for c, n in sorted(cells.items())]
    centers = kmeans(points, settings.zone_clusters_k, seed=0)
    calibration_id = uuid.uuid4()
    assigned: dict[uuid.UUID, int | None] = {}
    residuals: list[Residual] = []
    for r in logs:
        cluster = nearest(centers, Point(r.origin_lat, r.origin_lng))
        assigned[r.id] = cluster
        residuals.append(Residual(basis_of(r.source), r.time_bin, r.weekday_type, cluster, r.residual_ratio))
    results = calibrate(residuals, settings.calibration_min_n)
    now = datetime.now(UTC)
    async with session_scope(ctx.factory) as session:
        for i, c in enumerate(centers):
            n = sum(1 for v in assigned.values() if v == i)
            session.add(
                ZoneCluster(
                    cluster_id=i, lat=c.lat, lng=c.lng, n=n, calibration_id=calibration_id, created_at=now
                )
            )
        for f in results:
            session.add(
                FactorRow(
                    basis=f.basis,
                    level=f.level,
                    time_bin=f.time_bin,
                    weekday_type=f.weekday_type,
                    zone_cluster=f.zone_cluster,
                    r50=f.r50,
                    r80=f.r80,
                    r90=f.r90,
                    n=f.n,
                    calibration_id=calibration_id,
                    updated_at=now,
                )
            )
        by_cluster: dict[int | None, list[uuid.UUID]] = {}
        for log_id, cluster in assigned.items():
            by_cluster.setdefault(cluster, []).append(log_id)
        for cluster, ids in by_cluster.items():
            for i in range(0, len(ids), 1000):
                await session.execute(
                    update(EtaLog).where(EtaLog.id.in_(ids[i : i + 1000])).values(zone_cluster=cluster)
                )
        await session.flush()
        old = list(
            (
                await session.execute(
                    select(ZoneCluster.calibration_id, ZoneCluster.created_at)
                    .distinct()
                    .order_by(ZoneCluster.created_at.desc())
                )
            ).all()
        )
        stale = [row.calibration_id for row in old[KEEP_CALIBRATIONS:]]
        if stale:
            await session.execute(delete(FactorRow).where(FactorRow.calibration_id.in_(stale)))
            await session.execute(delete(ZoneCluster).where(ZoneCluster.calibration_id.in_(stale)))
    ctx.settings_cache.invalidate()
    levels = Counter(f.level for f in results)
    bases = Counter(f.basis for f in results)
    return {
        "calibration_id": str(calibration_id),
        "eta_log_rows": len(logs),
        "clusters": len(centers),
        "factor_rows": len(results),
        "by_level": dict(levels),
        "by_basis": dict(bases),
        "p80_coverage": await p80_coverage(ctx),
    }
