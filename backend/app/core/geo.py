"""Geometry helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass

import h3

EARTH_RADIUS_M = 6_371_008.8
H3_RES = 9


@dataclass(frozen=True, slots=True)
class Point:
    lat: float
    lng: float

    def h3(self, res: int = H3_RES) -> str:
        return str(h3.latlng_to_cell(self.lat, self.lng, res))

    def as_tuple(self) -> tuple[float, float]:
        return (self.lat, self.lng)


def haversine_m(a: Point, b: Point) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def offset_m(p: Point, north_m: float, east_m: float) -> Point:
    dlat = north_m / 111_320.0
    dlng = east_m / (111_320.0 * math.cos(math.radians(p.lat)))
    return Point(p.lat + dlat, p.lng + dlng)


def interpolate(a: Point, b: Point, frac: float) -> Point:
    f = min(1.0, max(0.0, frac))
    return Point(a.lat + (b.lat - a.lat) * f, a.lng + (b.lng - a.lng) * f)
