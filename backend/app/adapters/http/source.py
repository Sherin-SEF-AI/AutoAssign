"""HttpSource: reads from the BluRabbit User App, Driver App and Admin backends.

The contract lives in contracts/datasource.openapi.yaml. Every list endpoint is paginated
with {items, next_cursor}; every request carries X-API-Key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.adapters.base import (
    DriverRecord,
    HubRecord,
    PingRecord,
    SocCheckinRecord,
    TripEvent,
    TripRecord,
    VehicleRecord,
)
from app.config import Settings
from app.core.errors import ProviderError
from app.core.http import call_with_retries
from app.core.logging import get_logger

log = get_logger("adapters.http")
R = TypeVar("R", bound=BaseModel)
MAX_PAGES = 1000


class HttpSource:
    name = "http"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not (
            settings.upstream_userapp_url
            and settings.upstream_driverapp_url
            and settings.upstream_admin_url
            and settings.upstream_api_key
        ):
            raise ValueError("HttpSource requires the three upstream URLs and UPSTREAM_API_KEY")
        self._urls = {
            "userapp": settings.upstream_userapp_url.rstrip("/"),
            "driverapp": settings.upstream_driverapp_url.rstrip("/"),
            "admin": settings.upstream_admin_url.rstrip("/"),
        }
        self._retries = settings.http_retries
        self._backoff = settings.http_backoff_base_s
        self._client = httpx.AsyncClient(
            timeout=settings.upstream_timeout_s,
            headers={"X-API-Key": settings.upstream_api_key, "Accept": "application/json"},
            transport=transport,
        )

    async def _get_page(self, upstream: str, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self._urls[upstream]}{path}"
        response = await call_with_retries(
            f"upstream_{upstream}",
            lambda: self._client.get(url, params=params),
            retries=self._retries,
            backoff_base_s=self._backoff,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"upstream_{upstream}", f"non JSON response from {path}", retryable=False
            ) from exc
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise ProviderError(f"upstream_{upstream}", f"{path} response is missing items", retryable=False)
        return body

    async def _iter(
        self, upstream: str, path: str, params: dict[str, str], model: type[R]
    ) -> AsyncIterator[R]:
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            body = await self._get_page(upstream, path, query)
            for raw in body["items"]:
                try:
                    yield model.model_validate(raw)
                except ValidationError as exc:
                    raise ProviderError(
                        f"upstream_{upstream}",
                        f"{path} item violates contract: {exc.errors()[:3]}",
                        retryable=False,
                    ) from exc
            cursor = body.get("next_cursor")
            if not cursor:
                return
        raise ProviderError(f"upstream_{upstream}", f"{path} exceeded {MAX_PAGES} pages", retryable=False)

    async def _list(self, upstream: str, path: str, params: dict[str, str], model: type[R]) -> list[R]:
        return [item async for item in self._iter(upstream, path, params, model)]

    async def list_trips(self, service_date: date) -> list[TripRecord]:
        return await self._list(
            "userapp", "/v1/trips", {"service_date": service_date.isoformat()}, TripRecord
        )

    async def list_drivers(self, service_date: date) -> list[DriverRecord]:
        return await self._list(
            "driverapp", "/v1/drivers", {"service_date": service_date.isoformat()}, DriverRecord
        )

    async def list_vehicles(self) -> list[VehicleRecord]:
        return await self._list("admin", "/v1/vehicles", {}, VehicleRecord)

    async def list_hubs(self) -> list[HubRecord]:
        return await self._list("admin", "/v1/hubs", {}, HubRecord)

    async def list_soc_checkins(self, service_date: date) -> list[SocCheckinRecord]:
        return await self._list(
            "driverapp", "/v1/soc-checkins", {"service_date": service_date.isoformat()}, SocCheckinRecord
        )

    async def stream_pings(self, since: datetime) -> AsyncIterator[PingRecord]:
        async for item in self._iter("driverapp", "/v1/pings", {"since": since.isoformat()}, PingRecord):
            yield item

    async def trip_events(self, since: datetime) -> AsyncIterator[TripEvent]:
        async for item in self._iter("userapp", "/v1/trip-events", {"since": since.isoformat()}, TripEvent):
            yield item

    async def aclose(self) -> None:
        await self._client.aclose()
