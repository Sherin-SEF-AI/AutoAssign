"""HERE Routing v8 and Matrix Routing v8 clients with budget guard and circuit breakers.

The API key is only ever sent as the apiKey query parameter over HTTPS from the backend.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

import httpx

from app.adapters.here.budget import BudgetGuard
from app.core.circuit import CircuitBreaker
from app.core.errors import ProviderError, ProviderUnavailable
from app.core.geo import Point
from app.core.http import backoff_delay, call_with_retries
from app.core.logging import get_logger
from app.core.routing import RawLeg
from app.core.timeutil import iso_ist

log = get_logger("here")

MAX_MATRIX_SIDE = 50
REGION_CENTER = {"lat": 12.97, "lng": 77.59}
REGION_RADIUS_M = 60000
POLL_MAX_S = 60.0


def _loc(p: Point) -> str:
    return f"{p.lat:.6f},{p.lng:.6f}"


class HereClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        routing_url: str,
        matrix_url: str,
        budget: BudgetGuard,
        routing_breaker: CircuitBreaker,
        matrix_breaker: CircuitBreaker,
        retries: int,
        backoff_base_s: float,
        timeout_s: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if not routing_url.startswith("https://") or not matrix_url.startswith("https://"):
            raise ValueError("HERE endpoints must use HTTPS")
        self.client = client
        self.api_key = api_key
        self.routing_url = routing_url
        self.matrix_url = matrix_url
        self.budget = budget
        self.routing_breaker = routing_breaker
        self.matrix_breaker = matrix_breaker
        self.retries = retries
        self.backoff_base_s = backoff_base_s
        self.timeout = httpx.Timeout(timeout_s)
        self.sleep = sleep

    # Routing -------------------------------------------------------------------

    async def route(self, origin: Point, dest: Point, depart_at: datetime, now: datetime) -> RawLeg:
        if not await self.routing_breaker.allow():
            raise ProviderUnavailable("here_routing", "circuit open")
        if not await self.budget.try_consume({"here_routing": 1}, now):
            raise ProviderUnavailable("here_routing", "daily budget exhausted")
        params = {
            "transportMode": "car",
            "origin": _loc(origin),
            "destination": _loc(dest),
            "departureTime": iso_ist(depart_at),
            "return": "summary",
            "apiKey": self.api_key,
        }
        try:
            response = await call_with_retries(
                "here_routing",
                lambda: self.client.get(self.routing_url, params=params, timeout=self.timeout),
                retries=self.retries,
                backoff_base_s=self.backoff_base_s,
                sleep=self.sleep,
            )
            leg = self._parse_route(response)
        except ProviderError:
            await self.routing_breaker.record_failure()
            raise
        await self.routing_breaker.record_success()
        return leg

    @staticmethod
    def _parse_route(response: httpx.Response) -> RawLeg:
        try:
            body = response.json()
            route = body["routes"][0]
            duration = base = length = 0.0
            for section in route["sections"]:
                summary = section["summary"]
                duration += float(summary["duration"])
                base += float(summary.get("baseDuration", summary["duration"]))
                length += float(summary["length"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("here_routing", f"unexpected response shape: {exc}", retryable=False) from exc
        return RawLeg(
            duration_s=duration,
            distance_m=length,
            source="here",
            free_flow_s=base,
            provider_ref=str(route.get("id", "")) or None,
            payload={"duration": duration, "baseDuration": base, "length": length},
        )

    # Matrix --------------------------------------------------------------------

    async def matrix(
        self, origins: Sequence[Point], dests: Sequence[Point], depart_at: datetime, now: datetime
    ) -> list[list[RawLeg | None]]:
        if len(origins) > MAX_MATRIX_SIDE or len(dests) > MAX_MATRIX_SIDE:
            raise ValueError("matrix requests must be at most 50 by 50")
        if not origins or not dests:
            return [[] for _ in origins]
        if not await self.matrix_breaker.allow():
            raise ProviderUnavailable("here_matrix", "circuit open")
        elements = len(origins) * len(dests)
        if not await self.budget.try_consume({"here_matrix": 1, "here_matrix_elements": elements}, now):
            raise ProviderUnavailable("here_matrix", "daily budget exhausted")
        body = {
            "origins": [{"lat": p.lat, "lng": p.lng} for p in origins],
            "destinations": [{"lat": p.lat, "lng": p.lng} for p in dests],
            "regionDefinition": {"type": "circle", "center": REGION_CENTER, "radius": REGION_RADIUS_M},
            "departureTime": iso_ist(depart_at),
            "transportMode": "car",
            "matrixAttributes": ["travelTimes", "distances"],
        }
        try:
            response = await call_with_retries(
                "here_matrix",
                lambda: self.client.post(
                    self.matrix_url,
                    params={"async": "false", "apiKey": self.api_key},
                    json=body,
                    timeout=self.timeout,
                ),
                retries=self.retries,
                backoff_base_s=self.backoff_base_s,
                sleep=self.sleep,
            )
            if response.status_code == 202:
                payload = await self._poll(response)
            else:
                payload = self._json(response)
            result = self._parse_matrix(payload, len(origins), len(dests))
        except ProviderError:
            await self.matrix_breaker.record_failure()
            raise
        await self.matrix_breaker.record_success()
        return result

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("here_matrix", "non JSON response", retryable=False) from exc
        if not isinstance(data, dict):
            raise ProviderError("here_matrix", "unexpected response shape", retryable=False)
        return data

    async def _poll(self, accepted: httpx.Response) -> dict[str, Any]:
        info = self._json(accepted)
        status_url = info.get("statusUrl")
        if not status_url:
            raise ProviderError("here_matrix", "202 without statusUrl", retryable=False)
        waited = 0.0
        attempt = 0
        while waited < POLL_MAX_S:
            delay = max(0.25, backoff_delay(attempt, 0.5, cap_s=4.0))
            await self.sleep(delay)
            waited += delay
            attempt += 1
            status = await call_with_retries(
                "here_matrix",
                lambda: self.client.get(
                    status_url, params={"apiKey": self.api_key}, timeout=self.timeout, follow_redirects=False
                ),
                retries=self.retries,
                backoff_base_s=self.backoff_base_s,
                sleep=self.sleep,
            )
            result_url: str | None = None
            if status.status_code in (301, 302, 303, 307):
                result_url = status.headers.get("location")
            else:
                data = self._json(status)
                state = data.get("status")
                if state in ("failed", "error"):
                    raise ProviderError("here_matrix", f"matrix calculation failed: {data.get('error')}")
                if state == "completed":
                    result_url = data.get("resultUrl")
            if result_url:
                result = await call_with_retries(
                    "here_matrix",
                    lambda url=result_url: self.client.get(  # type: ignore[misc]
                        url, params={"apiKey": self.api_key}, timeout=self.timeout
                    ),
                    retries=self.retries,
                    backoff_base_s=self.backoff_base_s,
                    sleep=self.sleep,
                )
                return self._json(result)
        raise ProviderError("here_matrix", f"matrix not ready after {POLL_MAX_S:.0f}s")

    @staticmethod
    def _parse_matrix(payload: dict[str, Any], n_o: int, n_d: int) -> list[list[RawLeg | None]]:
        try:
            matrix = payload["matrix"]
            times = matrix["travelTimes"]
            dists = matrix.get("distances")
            errors = matrix.get("errorCodes")
            if int(matrix["numOrigins"]) != n_o or int(matrix["numDestinations"]) != n_d:
                raise ProviderError(
                    "here_matrix", "matrix dimensions do not match the request", retryable=False
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("here_matrix", f"unexpected response shape: {exc}", retryable=False) from exc
        ref = str(payload.get("matrixId", "")) or None
        out: list[list[RawLeg | None]] = []
        for o in range(n_o):
            row: list[RawLeg | None] = []
            for d in range(n_d):
                i = o * n_d + d
                if errors and errors[i] != 0:
                    row.append(None)
                    continue
                duration = float(times[i])
                distance = float(dists[i]) if dists else 0.0
                row.append(
                    RawLeg(
                        duration_s=duration,
                        distance_m=distance,
                        source="here",
                        provider_ref=ref,
                        payload={"travelTime": duration, "distance": distance, "matrixId": ref},
                    )
                )
            out.append(row)
        return out
