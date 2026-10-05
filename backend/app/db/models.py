"""SQLAlchemy ORM models. Every timestamp is timestamptz and stored in UTC."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
    }


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Snapshot(Base):
    __tablename__ = "snapshot"
    snapshot_id: Mapped[uuid.UUID] = _uuid_pk()
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("snapshot.snapshot_id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="ingest")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_snapshot_date_created", "service_date", "created_at"),)


class TripSnapshot(Base):
    __tablename__ = "trip_snapshot"
    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("snapshot.snapshot_id", ondelete="CASCADE"), nullable=False
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_pickup_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lng: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    drop_lat: Mapped[float] = mapped_column(Float, nullable=False)
    drop_lng: Mapped[float] = mapped_column(Float, nullable=False)
    drop_address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trip_type: Mapped[str] = mapped_column(String(16), nullable=False)
    package_hours: Mapped[float | None] = mapped_column(Float)
    pax: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    luggage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vehicle_class: Mapped[str] = mapped_column(String(16), nullable=False, default="any")
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    account_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actual_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_drop_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_distance_m: Mapped[float | None] = mapped_column(Float)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("snapshot_id", "trip_id", name="uq_trip_snapshot_trip"),
        Index("ix_trip_snapshot_date_pickup", "service_date", "scheduled_pickup_at"),
        Index("ix_trip_snapshot_trip", "trip_id"),
    )


class DriverSnapshot(Base):
    __tablename__ = "driver_snapshot"
    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("snapshot.snapshot_id", ondelete="CASCADE"), nullable=False
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    shift_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shift_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_lat: Mapped[float] = mapped_column(Float, nullable=False)
    start_lng: Mapped[float] = mapped_column(Float, nullable=False)
    end_lat: Mapped[float] = mapped_column(Float, nullable=False)
    end_lng: Mapped[float] = mapped_column(Float, nullable=False)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    approved_accounts: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    channel_today: Mapped[str] = mapped_column(String(16), nullable=False, default="blurabbit")
    max_hours: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    __table_args__ = (
        UniqueConstraint("snapshot_id", "driver_id", name="uq_driver_snapshot_driver"),
        Index("ix_driver_snapshot_date", "service_date"),
    )


class VehicleSnapshot(Base):
    __tablename__ = "vehicle_snapshot"
    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("snapshot.snapshot_id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    registration: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(32), nullable=False)
    variant: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    vehicle_class: Mapped[str] = mapped_column(String(16), nullable=False)
    battery_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    planning_cap_km: Mapped[float] = mapped_column(Float, nullable=False)
    e_roll_wh_per_km: Mapped[float] = mapped_column(Float, nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    luggage_class: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    hub_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    __table_args__ = (UniqueConstraint("snapshot_id", "vehicle_id", name="uq_vehicle_snapshot_vehicle"),)


class Hub(Base):
    __tablename__ = "hub"
    hub_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    ac_kw: Mapped[float] = mapped_column(Float, nullable=False)
    dc_kw: Mapped[float] = mapped_column(Float, nullable=False)
    ac_points: Mapped[int] = mapped_column(Integer, nullable=False)
    dc_points: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = _created()


class SocCheckin(Base):
    __tablename__ = "soc_checkin"
    id: Mapped[uuid.UUID] = _uuid_pk()
    driver_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vehicle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    soc_pct: Mapped[float] = mapped_column(Float, nullable=False)
    odometer_km: Mapped[float | None] = mapped_column(Float)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="datasource")
    __table_args__ = (UniqueConstraint("vehicle_id", "service_date", name="uq_soc_vehicle_date"),)


class Ping(Base):
    __tablename__ = "ping"
    driver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    speed_mps: Mapped[float | None] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (
        Index("ix_ping_driver_ts", "driver_id", text("ts DESC")),
        {"postgresql_partition_by": "RANGE (ts)"},
    )


class TripLive(Base):
    """Latest lifecycle state of a trip as reported by trip status events."""

    __tablename__ = "trip_live"
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lifecycle: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actual_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_drop_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_distance_m: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FleetException(Base):
    """Ops edits to vehicle status and driver shifts, applied on top of DataSource records."""

    __tablename__ = "fleet_exception"
    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_fleet_exception_date", "service_date", "kind"),)


class InboundEvent(Base):
    """Deduplication log for service-to-service trip events."""

    __tablename__ = "inbound_event"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = _created()


class LegEstimate(Base):
    __tablename__ = "leg_estimate"
    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_h3: Mapped[str] = mapped_column(String(16), nullable=False)
    dest_h3: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lng: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lng: Mapped[float] = mapped_column(Float, nullable=False)
    depart_bin: Mapped[int] = mapped_column(Integer, nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    p50_s: Mapped[float] = mapped_column(Float, nullable=False)
    p80_s: Mapped[float] = mapped_column(Float, nullable=False)
    p90_s: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    provider_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "kind", "origin_h3", "dest_h3", "depart_bin", "service_date", name="uq_leg_estimate_key"
        ),
        Index("ix_leg_estimate_source_expires", "source", "expires_at"),
    )


class ZoneCluster(Base):
    __tablename__ = "zone_cluster"
    id: Mapped[uuid.UUID] = _uuid_pk()
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    calibration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _created()


class FactorRow(Base):
    __tablename__ = "factor_table"
    id: Mapped[uuid.UUID] = _uuid_pk()
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    time_bin: Mapped[int | None] = mapped_column(Integer)
    weekday_type: Mapped[str | None] = mapped_column(String(8))
    zone_cluster: Mapped[int | None] = mapped_column(Integer)
    r50: Mapped[float] = mapped_column(Float, nullable=False)
    r80: Mapped[float] = mapped_column(Float, nullable=False)
    r90: Mapped[float] = mapped_column(Float, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    calibration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_factor_lookup", "basis", "time_bin", "weekday_type", "zone_cluster"),)


class Plan(Base):
    __tablename__ = "plan"
    plan_id: Mapped[uuid.UUID] = _uuid_pk()
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    parent_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan.plan_id", ondelete="RESTRICT"))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshot.snapshot_id"), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="nightly")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    solver_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    solver_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = _created()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("service_date", "version", name="uq_plan_date_version"),
        Index(
            "uq_plan_one_published",
            "service_date",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )


class PlanAssignment(Base):
    __tablename__ = "plan_assignment"
    id: Mapped[uuid.UUID] = _uuid_pk()
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan.plan_id", ondelete="CASCADE"), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    seq: Mapped[int | None] = mapped_column(Integer)
    planned_depart_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_arrive_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_drop_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadhead_s: Mapped[int | None] = mapped_column(Integer)
    deadhead_m: Mapped[int | None] = mapped_column(Integer)
    buffer_s: Mapped[int | None] = mapped_column(Integer)
    trip_s: Mapped[int | None] = mapped_column(Integer)
    trip_m: Mapped[int | None] = mapped_column(Integer)
    trip_p50_s: Mapped[int | None] = mapped_column(Integer)
    slack_s: Mapped[int | None] = mapped_column(Integer)
    energy_wh: Mapped[int | None] = mapped_column(Integer)
    trip_source: Mapped[str | None] = mapped_column(String(16))
    deadhead_source: Mapped[str | None] = mapped_column(String(16))
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unassigned_reason: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (
        UniqueConstraint("plan_id", "trip_id", name="uq_plan_assignment_trip"),
        Index("ix_plan_assignment_driver", "plan_id", "driver_id", "seq"),
    )


class PlanRoute(Base):
    """Per driver summary of a plan version, including the return leg."""

    __tablename__ = "plan_route"
    id: Mapped[uuid.UUID] = _uuid_pk()
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan.plan_id", ondelete="CASCADE"), nullable=False)
    driver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trip_count: Mapped[int] = mapped_column(Integer, nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_leg_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_leg_m: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    energy_wh: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    energy_cap_wh: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    soc_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (UniqueConstraint("plan_id", "driver_id", name="uq_plan_route_driver"),)


class Override(Base):
    __tablename__ = "override"
    override_id: Mapped[uuid.UUID] = _uuid_pk()
    plan_id_before: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan.plan_id"), nullable=False)
    plan_id_after: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan.plan_id"))
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="move")
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_driver_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    to_driver_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    feasibility: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_override_date_created", "service_date", "created_at"),)


class UnassignedAck(Base):
    __tablename__ = "unassigned_ack"
    id: Mapped[uuid.UUID] = _uuid_pk()
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan.plan_id"), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("service_date", "trip_id", "reason", name="uq_unassigned_ack"),)


class RepairEvent(Base):
    __tablename__ = "repair_event"
    id: Mapped[uuid.UUID] = _uuid_pk()
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="repaired")
    plan_id_before: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan.plan_id"))
    plan_id_after: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan.plan_id"))
    trip_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_repair_event_date", "service_date", "created_at"),)


class EtaLog(Base):
    __tablename__ = "eta_log"
    id: Mapped[uuid.UUID] = _uuid_pk()
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    leg_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    predicted_p50_s: Mapped[float | None] = mapped_column(Float)
    predicted_p80_s: Mapped[float | None] = mapped_column(Float)
    base_s: Mapped[float | None] = mapped_column(Float)
    actual_s: Mapped[float] = mapped_column(Float, nullable=False)
    residual_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    covered_p80: Mapped[bool | None] = mapped_column(Boolean)
    depart_bin: Mapped[int] = mapped_column(Integer, nullable=False)
    time_bin: Mapped[int] = mapped_column(Integer, nullable=False)
    weekday_type: Mapped[str] = mapped_column(String(8), nullable=False)
    zone_cluster: Mapped[int | None] = mapped_column(Integer)
    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lng: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("trip_id", "leg_kind", name="uq_eta_log_trip_leg"),
        Index("ix_eta_log_source_computed", "source", "computed_at"),
        Index("ix_eta_log_date", "service_date"),
    )


class JobRun(Base):
    __tablename__ = "job_run"
    id: Mapped[uuid.UUID] = _uuid_pk()
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    service_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_job_run_name_started", "job_name", "started_at"),)


class AppUser(Base):
    __tablename__ = "app_user"
    user_id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created()


class SettingOverride(Base):
    __tablename__ = "setting_override"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = _created()


class SettingAudit(Base):
    __tablename__ = "setting_audit"
    id: Mapped[uuid.UUID] = _uuid_pk()
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()


class ReplayRun(Base):
    __tablename__ = "replay_run"
    id: Mapped[uuid.UUID] = _uuid_pk()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    report_path: Mapped[str | None] = mapped_column(Text)


class MonitorState(Base):
    """Latest monitor result per driver, served by GET /live."""

    __tablename__ = "monitor_state"
    service_date: Mapped[date] = mapped_column(Date, primary_key=True)
    driver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_trip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    next_trip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    predicted_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slack_s: Mapped[int | None] = mapped_column(Integer)
    risk: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str | None] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
