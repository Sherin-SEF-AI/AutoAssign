"""Quantile factor table with hierarchical back-off and zone clusters.

Factors are ratios of actual duration to the provider's raw duration, per basis:
  here      residuals against HERE traffic-aware durations
  osrm      residuals against OSRM free-flow durations (carries the congestion factor itself)
  fallback  residuals against the haversine speed-profile estimate
Lookup order: (time_bin, weekday_type, zone_cluster) -> (time_bin, weekday_type) -> (time_bin) -> global
-> configured defaults.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.geo import Point, haversine_m
from app.core.speed import free_flow_kmh
from app.db.models import FactorRow, ZoneCluster

BASES = ("here", "osrm", "fallback")


@dataclass(frozen=True, slots=True)
class Factors:
    r50: float
    r80: float
    r90: float
    level: str
    n: int = 0


@dataclass(slots=True)
class FactorTable:
    defaults: dict[str, dict[int, Factors]]
    rows: dict[tuple[str, int | None, str | None, int | None], Factors] = field(default_factory=dict)
    centroids: list[tuple[int, Point]] = field(default_factory=list)
    calibration_id: str | None = None

    @property
    def calibrated(self) -> bool:
        return bool(self.rows)

    def cluster_of(self, p: Point) -> int | None:
        if not self.centroids:
            return None
        return min(self.centroids, key=lambda c: (haversine_m(p, c[1]), c[0]))[0]

    def lookup(self, basis: str, time_bin: int, weekday_type: str, cluster: int | None) -> Factors:
        keys: list[tuple[str, int | None, str | None, int | None]] = []
        if cluster is not None:
            keys.append((basis, time_bin, weekday_type, cluster))
        keys += [
            (basis, time_bin, weekday_type, None),
            (basis, time_bin, None, None),
            (basis, None, None, None),
        ]
        for key in keys:
            hit = self.rows.get(key)
            if hit is not None:
                return hit
        return self.defaults[basis][time_bin]


def default_factors(settings: Settings) -> dict[str, dict[int, Factors]]:
    profile = settings.speed_profile_json
    free = free_flow_kmh(profile)
    base = Factors(
        settings.factor_r50_default, settings.factor_r80_default, settings.factor_r90_default, "default"
    )
    out: dict[str, dict[int, Factors]] = {"here": {}, "fallback": {}, "osrm": {}}
    for i, b in enumerate(profile):
        out["here"][i] = base
        out["fallback"][i] = base
        congestion = free / b.kmh
        out["osrm"][i] = Factors(
            round(congestion * settings.factor_r50_default, 4),
            round(congestion * settings.factor_r80_default, 4),
            round(congestion * settings.factor_r90_default, 4),
            "default",
        )
    return out


async def load_factor_table(session: AsyncSession, settings: Settings) -> FactorTable:
    table = FactorTable(defaults=default_factors(settings))
    latest = (
        await session.execute(
            select(FactorRow.calibration_id).order_by(FactorRow.updated_at.desc(), FactorRow.id).limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None:
        rows = (await session.execute(select(FactorRow).where(FactorRow.calibration_id == latest))).scalars()
        for r in rows:
            table.rows[(r.basis, r.time_bin, r.weekday_type, r.zone_cluster)] = Factors(
                r.r50, r.r80, r.r90, r.level, r.n
            )
        table.calibration_id = str(latest)
    cluster_cal = (
        await session.execute(
            select(ZoneCluster.calibration_id).order_by(ZoneCluster.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if cluster_cal is not None:
        clusters = (
            await session.execute(
                select(ZoneCluster)
                .where(ZoneCluster.calibration_id == cluster_cal)
                .order_by(ZoneCluster.cluster_id)
            )
        ).scalars()
        table.centroids = [(c.cluster_id, Point(c.lat, c.lng)) for c in clusters]
    return table


async def factor_row_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(FactorRow))).scalar_one())


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation quantile over an already sorted sequence."""
    if not sorted_values:
        raise ValueError("quantile of empty sequence")
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo))
