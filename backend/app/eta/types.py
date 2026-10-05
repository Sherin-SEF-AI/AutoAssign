"""ETA value types shared by every caller."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.core.geo import Point
from app.core.routing import RawLeg
from app.core.timeutil import depart_bin, service_date_of

LegKind = Literal["trip", "deadhead", "hub"]
Source = Literal["cache", "here", "osrm", "fallback"]
KINDS: tuple[LegKind, ...] = ("trip", "deadhead", "hub")


@dataclass(frozen=True, slots=True)
class LegRequest:
    origin: Point
    dest: Point
    depart_at: datetime


@dataclass(frozen=True, slots=True)
class CacheKey:
    kind: str
    origin_h3: str
    dest_h3: str
    depart_bin: int
    service_date: date

    def redis_key(self) -> str:
        day = self.service_date.isoformat()
        return f"eta:hot:{self.kind}:{self.origin_h3}:{self.dest_h3}:{self.depart_bin}:{day}"


def cache_key(kind: str, req: LegRequest) -> CacheKey:
    return CacheKey(
        kind=kind,
        origin_h3=req.origin.h3(),
        dest_h3=req.dest.h3(),
        depart_bin=depart_bin(req.depart_at),
        service_date=service_date_of(req.depart_at),
    )


@dataclass(frozen=True, slots=True)
class LegEstimate:
    p50_s: float
    p80_s: float
    p90_s: float
    distance_m: float
    source: str
    provider_ref: str | None = None
    base_s: float = 0.0
    cached: bool = False

    def as_ints(self) -> tuple[int, int, int, int]:
        return round(self.p50_s), round(self.p80_s), round(self.p90_s), round(self.distance_m)


__all__ = ["KINDS", "CacheKey", "LegEstimate", "LegKind", "LegRequest", "RawLeg", "Source", "cache_key"]
