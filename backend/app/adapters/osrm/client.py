"""OSRM table and route clients (free-flow durations)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.core.circuit import CircuitBreaker
from app.core.errors import ProviderError, ProviderUnavailable
from app.core.geo import Point
from app.core.http import call_with_retries
from app.core.routing import RawLeg

MAX_TABLE_SIDE = 100


def _coords(points: Sequence[Point]) -> str:
    return ";".join(f"{p.lng:.6f},{p.lat:.6f}" for p in points)


class OsrmClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        breaker: CircuitBreaker,
        retries: int,
        backoff_base_s: float,
        timeout_s: float,
    ):
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.breaker = breaker
        self.retries = retries
        self.backoff_base_s = backoff_base_s
        self.timeout = httpx.Timeout(timeout_s)

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not await self.breaker.allow():
            raise ProviderUnavailable("osrm", "circuit open")
        try:
            response = await call_with_retries(
                "osrm",
                lambda: self.client.get(f"{self.base_url}{path}", params=params, timeout=self.timeout),
                retries=self.retries,
                backoff_base_s=self.backoff_base_s,
            )
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderError("osrm", "non JSON response", retryable=False) from exc
            if not isinstance(body, dict) or body.get("code") != "Ok":
                code = body.get("code") if isinstance(body, dict) else "invalid"
                raise ProviderError("osrm", f"response code {code}", retryable=False)
        except ProviderError:
            await self.breaker.record_failure()
            raise
        await self.breaker.record_success()
        return body

    async def route(self, origin: Point, dest: Point) -> RawLeg:
        body = await self._get(f"/route/v1/driving/{_coords([origin, dest])}", {"overview": "false"})
        try:
            route = body["routes"][0]
            duration, distance = float(route["duration"]), float(route["distance"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("osrm", f"unexpected route shape: {exc}", retryable=False) from exc
        return RawLeg(duration_s=duration, distance_m=distance, source="osrm", free_flow_s=duration)

    async def geometry(self, origin: Point, dest: Point) -> tuple[list[Point], float, float]:
        """Route polyline for the simulator: (points, duration_s, distance_m)."""
        body = await self._get(
            f"/route/v1/driving/{_coords([origin, dest])}", {"overview": "full", "geometries": "geojson"}
        )
        try:
            route = body["routes"][0]
            coords = route["geometry"]["coordinates"]
            points = [Point(float(lat), float(lng)) for lng, lat in coords]
            return points, float(route["duration"]), float(route["distance"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("osrm", f"unexpected geometry shape: {exc}", retryable=False) from exc

    async def table(self, origins: Sequence[Point], dests: Sequence[Point]) -> list[list[RawLeg | None]]:
        if len(origins) > MAX_TABLE_SIDE or len(dests) > MAX_TABLE_SIDE:
            raise ValueError("OSRM table requests must be at most 100 by 100")
        if not origins or not dests:
            return [[] for _ in origins]
        points = [*origins, *dests]
        params = {
            "sources": ";".join(str(i) for i in range(len(origins))),
            "destinations": ";".join(str(len(origins) + j) for j in range(len(dests))),
            "annotations": "duration,distance",
        }
        body = await self._get(f"/table/v1/driving/{_coords(points)}", params)
        try:
            durations = body["durations"]
            distances = body.get("distances")
        except (KeyError, TypeError) as exc:
            raise ProviderError("osrm", f"unexpected table shape: {exc}", retryable=False) from exc
        out: list[list[RawLeg | None]] = []
        for i in range(len(origins)):
            row: list[RawLeg | None] = []
            for j in range(len(dests)):
                dur = durations[i][j]
                if dur is None:
                    row.append(None)
                    continue
                dist = distances[i][j] if distances else None
                row.append(
                    RawLeg(
                        duration_s=float(dur),
                        distance_m=float(dist) if dist is not None else 0.0,
                        source="osrm",
                        free_flow_s=float(dur),
                    )
                )
            out.append(row)
        return out
