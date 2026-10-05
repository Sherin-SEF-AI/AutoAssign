"""Adapter protocols and the canonical records that cross the adapter boundary.

Everything above the adapters layer sees only these records. The same shapes are
published for the BluRabbit upstream backends in contracts/datasource.openapi.yaml.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

Channel = Literal["blurabbit", "ets", "uber"]
TripType = Literal["ets", "airport", "city", "package"]
TripStatus = Literal[
    "booked", "assigned", "en_route", "arrived", "started", "completed", "cancelled", "no_show"
]
VehicleClass = Literal["any", "sedan", "suv"]
VehicleStatus = Literal["active", "service", "retired"]
TripEventType = Literal["trip.created", "trip.updated", "trip.cancelled", "trip.status"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("timestamps must carry a timezone offset")
    return v


class TripRecord(_Record):
    trip_id: uuid.UUID
    service_date: date
    channel: Channel
    scheduled_pickup_at: datetime
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lng: float = Field(ge=-180, le=180)
    pickup_address: str = ""
    drop_lat: float = Field(ge=-90, le=90)
    drop_lng: float = Field(ge=-180, le=180)
    drop_address: str = ""
    trip_type: TripType
    package_hours: float | None = Field(default=None, gt=0, le=24)
    pax: int = Field(default=1, ge=1, le=8)
    luggage: int = Field(default=0, ge=0, le=10)
    vehicle_class: VehicleClass = "any"
    tags: dict[str, Any] = Field(default_factory=dict)
    account_id: str | None = None
    status: TripStatus = "booked"
    lifecycle: dict[str, datetime] = Field(default_factory=dict)
    actual_pickup_at: datetime | None = None
    actual_drop_at: datetime | None = None
    actual_distance_m: float | None = Field(default=None, ge=0)

    _v1 = field_validator("scheduled_pickup_at", "actual_pickup_at", "actual_drop_at")(_aware)


class DriverRecord(_Record):
    driver_id: uuid.UUID
    service_date: date
    name: str
    active: bool = True
    shift_start_at: datetime | None = None
    shift_end_at: datetime | None = None
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float
    vehicle_id: uuid.UUID | None = None
    skills: list[str] = Field(default_factory=list)
    approved_accounts: list[str] = Field(default_factory=list)
    channel_today: Channel = "blurabbit"
    max_hours: float = Field(default=10.0, gt=0, le=24)

    _v1 = field_validator("shift_start_at", "shift_end_at")(_aware)


class VehicleRecord(_Record):
    vehicle_id: uuid.UUID
    registration: str = ""
    model: str
    variant: str = ""
    vehicle_class: Literal["sedan", "suv"]
    battery_kwh: float = Field(gt=0)
    seats: int = Field(default=4, ge=1)
    luggage_class: str = "medium"
    status: VehicleStatus = "active"
    hub_id: uuid.UUID | None = None


class HubRecord(_Record):
    hub_id: uuid.UUID
    name: str
    lat: float
    lng: float
    ac_kw: float = Field(ge=0)
    dc_kw: float = Field(ge=0)
    ac_points: int = Field(ge=0)
    dc_points: int = Field(ge=0)


class SocCheckinRecord(_Record):
    driver_id: uuid.UUID | None = None
    vehicle_id: uuid.UUID
    service_date: date
    soc_pct: float = Field(ge=0, le=100)
    odometer_km: float | None = Field(default=None, ge=0)
    reported_at: datetime

    _v1 = field_validator("reported_at")(_aware)


class PingRecord(_Record):
    driver_id: uuid.UUID
    ts: datetime
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    speed_mps: float | None = Field(default=None, ge=0)
    accuracy_m: float | None = Field(default=None, ge=0)

    _v1 = field_validator("ts")(_aware)


class TripEvent(_Record):
    event_id: str = Field(min_length=1, max_length=128)
    event_type: TripEventType
    occurred_at: datetime
    trip: TripRecord | None = None
    trip_id: uuid.UUID | None = None
    service_date: date | None = None
    status: TripStatus | None = None
    driver_id: uuid.UUID | None = None
    actual_pickup_at: datetime | None = None
    actual_drop_at: datetime | None = None
    actual_distance_m: float | None = Field(default=None, ge=0)

    _v1 = field_validator("occurred_at", "actual_pickup_at", "actual_drop_at")(_aware)

    def resolved_trip_id(self) -> uuid.UUID:
        if self.trip is not None:
            return self.trip.trip_id
        if self.trip_id is None:
            raise ValueError("trip event without trip or trip_id")
        return self.trip_id

    def resolved_service_date(self) -> date:
        if self.trip is not None:
            return self.trip.service_date
        if self.service_date is None:
            raise ValueError("trip event without service_date")
        return self.service_date


@runtime_checkable
class DataSource(Protocol):
    """Read side of the BluRabbit backends (User App, Driver App, Admin)."""

    name: str

    async def list_trips(self, service_date: date) -> list[TripRecord]: ...

    async def list_drivers(self, service_date: date) -> list[DriverRecord]: ...

    async def list_vehicles(self) -> list[VehicleRecord]: ...

    async def list_hubs(self) -> list[HubRecord]: ...

    async def list_soc_checkins(self, service_date: date) -> list[SocCheckinRecord]: ...

    def stream_pings(self, since: datetime) -> AsyncIterator[PingRecord]: ...

    def trip_events(self, since: datetime) -> AsyncIterator[TripEvent]: ...

    async def aclose(self) -> None: ...


class Notification(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: str
    service_date: date | None = None
    plan_id: uuid.UUID | None = None
    data: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Notifier(Protocol):
    name: str

    async def send(self, notification: Notification) -> None: ...


@runtime_checkable
class Regenerable(Protocol):
    """Optional capability: a source whose dataset can be rebuilt on demand (synthetic only)."""

    async def regenerate(
        self,
        factory: Any,
        settings: Any,
        *,
        seed: int,
        days_past: int,
        days_future: int,
        today: date,
        progress: Any = None,
    ) -> dict[str, Any]: ...
