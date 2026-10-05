"""Shared API response models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.plan.readmodels import TripRow


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


TripOut = TripRow


class EstimateOut(ORM):
    kind: str
    source: str
    depart_bin: int
    p50_s: float
    p80_s: float
    p90_s: float
    distance_m: float
    created_at: datetime
    expires_at: datetime | None


class EtaLogOut(ORM):
    leg_kind: str
    source: str
    predicted_p50_s: float | None
    predicted_p80_s: float | None
    actual_s: float
    residual_ratio: float
    covered_p80: bool | None
    computed_at: datetime


class TripDetailOut(BaseModel):
    trip: TripOut
    lifecycle: dict[str, Any]
    estimates: list[EstimateOut]
    eta_log: list[EtaLogOut]
    snapshot_id: uuid.UUID


class DriverOut(ORM):
    driver_id: uuid.UUID
    service_date: date
    name: str
    active: bool
    shift_start_at: datetime | None
    shift_end_at: datetime | None
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float
    vehicle_id: uuid.UUID | None
    skills: list[str]
    approved_accounts: list[str]
    channel_today: str
    max_hours: float
    vehicle_model: str | None = None
    vehicle_registration: str | None = None
    vehicle_status: str | None = None
    soc_pct: float | None = None
    odometer_km: float | None = None
    soc_reported_at: datetime | None = None
    assigned_trips: int = 0


class VehicleOut(ORM):
    vehicle_id: uuid.UUID
    registration: str
    model: str
    variant: str
    vehicle_class: str
    battery_kwh: float
    planning_cap_km: float
    e_roll_wh_per_km: float
    seats: int
    luggage_class: str
    status: str
    hub_id: uuid.UUID | None
    hub_name: str | None = None
    driver_id: uuid.UUID | None = None
    driver_name: str | None = None
    soc_pct: float | None = None


class SnapshotOut(ORM):
    snapshot_id: uuid.UUID
    service_date: date
    source: str
    reason: str
    content_hash: str
    counts: dict[str, Any]
    parent_snapshot_id: uuid.UUID | None
    created_at: datetime


class HubOut(ORM):
    hub_id: uuid.UUID
    name: str
    lat: float
    lng: float
    ac_kw: float
    dc_kw: float
    ac_points: int
    dc_points: int


class JobRunOut(ORM):
    id: uuid.UUID
    job_name: str
    service_date: date | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    stats: dict[str, Any]
    error: str | None
