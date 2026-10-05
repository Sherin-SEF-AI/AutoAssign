"""Factor calibration from eta_log residuals, and the zone clustering it uses."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.geo import Point, haversine_m
from app.eta.factors import quantile

LEVELS = ("cell", "bin_weekday", "bin", "global")


@dataclass(frozen=True, slots=True)
class Residual:
    basis: str
    time_bin: int
    weekday_type: str
    zone_cluster: int | None
    ratio: float


@dataclass(frozen=True, slots=True)
class FactorResult:
    basis: str
    level: str
    time_bin: int | None
    weekday_type: str | None
    zone_cluster: int | None
    r50: float
    r80: float
    r90: float
    n: int


def basis_of(source: str) -> str:
    return source if source in ("here", "osrm") else "fallback"


def kmeans(
    points: Sequence[tuple[Point, int]], k: int, *, seed: int = 0, iterations: int = 60
) -> list[Point]:
    """Weighted k-means with k-means++ seeding. Deterministic for a given input order and seed."""
    pts = sorted(points, key=lambda pw: (pw[0].lat, pw[0].lng))
    if not pts:
        return []
    k = min(k, len(pts))
    rng = random.Random(seed)
    centers = [pts[rng.randrange(len(pts))][0]]
    while len(centers) < k:
        weights = [w * min(haversine_m(p, c) for c in centers) ** 2 for p, w in pts]
        total = sum(weights)
        if total <= 0:
            break
        r = rng.random() * total
        acc = 0.0
        for (p, _), w in zip(pts, weights, strict=True):
            acc += w
            if acc >= r:
                centers.append(p)
                break
    for _ in range(iterations):
        sums: list[list[float]] = [[0.0, 0.0, 0.0] for _ in centers]
        for p, w in pts:
            idx = min(range(len(centers)), key=lambda i: (haversine_m(p, centers[i]), i))
            sums[idx][0] += p.lat * w
            sums[idx][1] += p.lng * w
            sums[idx][2] += w
        moved = [Point(s[0] / s[2], s[1] / s[2]) if s[2] > 0 else centers[i] for i, s in enumerate(sums)]
        if all(haversine_m(a, b) < 1.0 for a, b in zip(moved, centers, strict=True)):
            centers = moved
            break
        centers = moved
    return centers


def nearest(centers: Sequence[Point], p: Point) -> int | None:
    if not centers:
        return None
    return min(range(len(centers)), key=lambda i: (haversine_m(p, centers[i]), i))


def _factors(ratios: list[float]) -> tuple[float, float, float]:
    s = sorted(ratios)
    r50 = quantile(s, 0.5)
    r80 = max(quantile(s, 0.8), r50)
    r90 = max(quantile(s, 0.9), r80)
    return round(r50, 4), round(r80, 4), round(r90, 4)


def calibrate(residuals: Sequence[Residual], min_n: int) -> list[FactorResult]:
    """Emit factors at every level that has at least min_n samples."""
    groups: dict[tuple[str, str, int | None, str | None, int | None], list[float]] = defaultdict(list)
    for r in residuals:
        if r.ratio <= 0:
            continue
        if r.zone_cluster is not None:
            groups[(r.basis, "cell", r.time_bin, r.weekday_type, r.zone_cluster)].append(r.ratio)
        groups[(r.basis, "bin_weekday", r.time_bin, r.weekday_type, None)].append(r.ratio)
        groups[(r.basis, "bin", r.time_bin, None, None)].append(r.ratio)
        groups[(r.basis, "global", None, None, None)].append(r.ratio)
    out: list[FactorResult] = []
    for (basis, level, tb, wd, zc), ratios in sorted(
        groups.items(), key=lambda kv: tuple("" if x is None else str(x) for x in kv[0])
    ):
        if len(ratios) < min_n:
            continue
        r50, r80, r90 = _factors(ratios)
        out.append(FactorResult(basis, level, tb, wd, zc, r50, r80, r90, len(ratios)))
    return out
