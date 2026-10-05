"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "eta_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("leg_kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("predicted_p50_s", sa.Float(), nullable=True),
        sa.Column("predicted_p80_s", sa.Float(), nullable=True),
        sa.Column("actual_s", sa.Float(), nullable=False),
        sa.Column("residual_ratio", sa.Float(), nullable=False),
        sa.Column("covered_p80", sa.Boolean(), nullable=True),
        sa.Column("depart_bin", sa.Integer(), nullable=False),
        sa.Column("time_bin", sa.Integer(), nullable=False),
        sa.Column("weekday_type", sa.String(length=8), nullable=False),
        sa.Column("zone_cluster", sa.Integer(), nullable=True),
        sa.Column("origin_lat", sa.Float(), nullable=False),
        sa.Column("origin_lng", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trip_id", "leg_kind", name="uq_eta_log_trip_leg"),
    )
    op.create_index("ix_eta_log_date", "eta_log", ["service_date"], unique=False)
    op.create_index("ix_eta_log_source_computed", "eta_log", ["source", "computed_at"], unique=False)
    op.create_table(
        "factor_table",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("basis", sa.String(length=16), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("time_bin", sa.Integer(), nullable=True),
        sa.Column("weekday_type", sa.String(length=8), nullable=True),
        sa.Column("zone_cluster", sa.Integer(), nullable=True),
        sa.Column("r50", sa.Float(), nullable=False),
        sa.Column("r80", sa.Float(), nullable=False),
        sa.Column("r90", sa.Float(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("calibration_id", sa.UUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_factor_lookup",
        "factor_table",
        ["basis", "time_bin", "weekday_type", "zone_cluster"],
        unique=False,
    )
    op.create_table(
        "fleet_exception",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fleet_exception_date", "fleet_exception", ["service_date", "kind"], unique=False)
    op.create_table(
        "hub",
        sa.Column("hub_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("ac_kw", sa.Float(), nullable=False),
        sa.Column("dc_kw", sa.Float(), nullable=False),
        sa.Column("ac_points", sa.Integer(), nullable=False),
        sa.Column("dc_points", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("hub_id"),
    )
    op.create_table(
        "inbound_event",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "job_run",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_name", sa.String(length=64), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_run_name_started", "job_run", ["job_name", "started_at"], unique=False)
    op.create_table(
        "leg_estimate",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("origin_h3", sa.String(length=16), nullable=False),
        sa.Column("dest_h3", sa.String(length=16), nullable=False),
        sa.Column("origin_lat", sa.Float(), nullable=False),
        sa.Column("origin_lng", sa.Float(), nullable=False),
        sa.Column("dest_lat", sa.Float(), nullable=False),
        sa.Column("dest_lng", sa.Float(), nullable=False),
        sa.Column("depart_bin", sa.Integer(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("p50_s", sa.Float(), nullable=False),
        sa.Column("p80_s", sa.Float(), nullable=False),
        sa.Column("p90_s", sa.Float(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("provider_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kind", "origin_h3", "dest_h3", "depart_bin", "service_date", name="uq_leg_estimate_key"
        ),
    )
    op.create_index("ix_leg_estimate_source_expires", "leg_estimate", ["source", "expires_at"], unique=False)
    op.create_table(
        "monitor_state",
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sim_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("ping_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_trip_id", sa.UUID(), nullable=True),
        sa.Column("next_trip_id", sa.UUID(), nullable=True),
        sa.Column("predicted_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("slack_s", sa.Integer(), nullable=True),
        sa.Column("risk", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("service_date", "driver_id"),
    )
    op.create_table(
        "ping",
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("speed_mps", sa.Float(), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("driver_id", "ts"),
        postgresql_partition_by="RANGE (ts)",
    )
    op.create_index("ix_ping_driver_ts", "ping", ["driver_id", sa.literal_column("ts DESC")], unique=False)
    op.execute("CREATE TABLE ping_default PARTITION OF ping DEFAULT")
    op.create_table(
        "replay_run",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "setting_audit",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "setting_override",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "snapshot",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("parent_snapshot_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_snapshot_id"], ["snapshot.snapshot_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index("ix_snapshot_date_created", "snapshot", ["service_date", "created_at"], unique=False)
    op.create_table(
        "soc_checkin",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=True),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("soc_pct", sa.Float(), nullable=False),
        sa.Column("odometer_km", sa.Float(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vehicle_id", "service_date", name="uq_soc_vehicle_date"),
    )
    op.create_table(
        "trip_live",
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=True),
        sa.Column("lifecycle", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actual_pickup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_drop_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_distance_m", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trip_id"),
    )
    op.create_index(op.f("ix_trip_live_service_date"), "trip_live", ["service_date"], unique=False)
    op.create_table(
        "zone_cluster",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("calibration_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "driver_snapshot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("shift_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shift_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_lat", sa.Float(), nullable=False),
        sa.Column("start_lng", sa.Float(), nullable=False),
        sa.Column("end_lat", sa.Float(), nullable=False),
        sa.Column("end_lng", sa.Float(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("skills", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("approved_accounts", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("channel_today", sa.String(length=16), nullable=False),
        sa.Column("max_hours", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshot.snapshot_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "driver_id", name="uq_driver_snapshot_driver"),
    )
    op.create_index("ix_driver_snapshot_date", "driver_snapshot", ["service_date"], unique=False)
    op.create_table(
        "plan",
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("parent_plan_id", sa.UUID(), nullable=True),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("solver_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("solver_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_plan_id"], ["plan.plan_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshot.snapshot_id"],
        ),
        sa.PrimaryKeyConstraint("plan_id"),
        sa.UniqueConstraint("service_date", "version", name="uq_plan_date_version"),
    )
    op.create_index(
        "uq_plan_one_published",
        "plan",
        ["service_date"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "trip_snapshot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("scheduled_pickup_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pickup_lat", sa.Float(), nullable=False),
        sa.Column("pickup_lng", sa.Float(), nullable=False),
        sa.Column("pickup_address", sa.Text(), nullable=False),
        sa.Column("drop_lat", sa.Float(), nullable=False),
        sa.Column("drop_lng", sa.Float(), nullable=False),
        sa.Column("drop_address", sa.Text(), nullable=False),
        sa.Column("trip_type", sa.String(length=16), nullable=False),
        sa.Column("package_hours", sa.Float(), nullable=True),
        sa.Column("pax", sa.Integer(), nullable=False),
        sa.Column("luggage", sa.Integer(), nullable=False),
        sa.Column("vehicle_class", sa.String(length=16), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lifecycle", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actual_pickup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_drop_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_distance_m", sa.Float(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshot.snapshot_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "trip_id", name="uq_trip_snapshot_trip"),
    )
    op.create_index(
        "ix_trip_snapshot_date_pickup", "trip_snapshot", ["service_date", "scheduled_pickup_at"], unique=False
    )
    op.create_index("ix_trip_snapshot_trip", "trip_snapshot", ["trip_id"], unique=False)
    op.create_table(
        "vehicle_snapshot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("registration", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=32), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=False),
        sa.Column("vehicle_class", sa.String(length=16), nullable=False),
        sa.Column("battery_kwh", sa.Float(), nullable=False),
        sa.Column("planning_cap_km", sa.Float(), nullable=False),
        sa.Column("e_roll_wh_per_km", sa.Float(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("luggage_class", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("hub_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshot.snapshot_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "vehicle_id", name="uq_vehicle_snapshot_vehicle"),
    )
    op.create_table(
        "override",
        sa.Column("override_id", sa.UUID(), nullable=False),
        sa.Column("plan_id_before", sa.UUID(), nullable=False),
        sa.Column("plan_id_after", sa.UUID(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("from_driver_id", sa.UUID(), nullable=True),
        sa.Column("to_driver_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("feasibility", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id_after"],
            ["plan.plan_id"],
        ),
        sa.ForeignKeyConstraint(
            ["plan_id_before"],
            ["plan.plan_id"],
        ),
        sa.PrimaryKeyConstraint("override_id"),
    )
    op.create_index("ix_override_date_created", "override", ["service_date", "created_at"], unique=False)
    op.create_table(
        "plan_assignment",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=True),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("planned_depart_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_arrive_pickup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_pickup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_drop_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadhead_s", sa.Integer(), nullable=True),
        sa.Column("deadhead_m", sa.Integer(), nullable=True),
        sa.Column("buffer_s", sa.Integer(), nullable=True),
        sa.Column("trip_s", sa.Integer(), nullable=True),
        sa.Column("trip_m", sa.Integer(), nullable=True),
        sa.Column("trip_p50_s", sa.Integer(), nullable=True),
        sa.Column("slack_s", sa.Integer(), nullable=True),
        sa.Column("energy_wh", sa.Integer(), nullable=True),
        sa.Column("trip_source", sa.String(length=16), nullable=True),
        sa.Column("deadhead_source", sa.String(length=16), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("unassigned_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.plan_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "trip_id", name="uq_plan_assignment_trip"),
    )
    op.create_index(
        "ix_plan_assignment_driver", "plan_assignment", ["plan_id", "driver_id", "seq"], unique=False
    )
    op.create_table(
        "plan_route",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("trip_count", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_leg_s", sa.Integer(), nullable=False),
        sa.Column("end_leg_m", sa.Integer(), nullable=False),
        sa.Column("energy_wh", sa.Integer(), nullable=False),
        sa.Column("energy_cap_wh", sa.Integer(), nullable=False),
        sa.Column("soc_fraction", sa.Float(), nullable=False),
        sa.Column("distance_m", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.plan_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "driver_id", name="uq_plan_route_driver"),
    )
    op.create_table(
        "repair_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("plan_id_before", sa.UUID(), nullable=True),
        sa.Column("plan_id_after", sa.UUID(), nullable=True),
        sa.Column("trip_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id_after"],
            ["plan.plan_id"],
        ),
        sa.ForeignKeyConstraint(
            ["plan_id_before"],
            ["plan.plan_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repair_event_date", "repair_event", ["service_date", "created_at"], unique=False)
    op.create_table(
        "unassigned_ack",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plan.plan_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_date", "trip_id", "reason", name="uq_unassigned_ack"),
    )


def downgrade() -> None:
    op.drop_table("unassigned_ack")
    op.drop_index("ix_repair_event_date", table_name="repair_event")
    op.drop_table("repair_event")
    op.drop_table("plan_route")
    op.drop_index("ix_plan_assignment_driver", table_name="plan_assignment")
    op.drop_table("plan_assignment")
    op.drop_index("ix_override_date_created", table_name="override")
    op.drop_table("override")
    op.drop_table("vehicle_snapshot")
    op.drop_index("ix_trip_snapshot_trip", table_name="trip_snapshot")
    op.drop_index("ix_trip_snapshot_date_pickup", table_name="trip_snapshot")
    op.drop_table("trip_snapshot")
    op.drop_index(
        "uq_plan_one_published", table_name="plan", postgresql_where=sa.text("status = 'published'")
    )
    op.drop_table("plan")
    op.drop_index("ix_driver_snapshot_date", table_name="driver_snapshot")
    op.drop_table("driver_snapshot")
    op.drop_table("zone_cluster")
    op.drop_index(op.f("ix_trip_live_service_date"), table_name="trip_live")
    op.drop_table("trip_live")
    op.drop_table("soc_checkin")
    op.drop_index("ix_snapshot_date_created", table_name="snapshot")
    op.drop_table("snapshot")
    op.drop_table("setting_override")
    op.drop_table("setting_audit")
    op.drop_table("replay_run")
    op.drop_index("ix_ping_driver_ts", table_name="ping")
    op.drop_table("ping")
    op.drop_table("monitor_state")
    op.drop_index("ix_leg_estimate_source_expires", table_name="leg_estimate")
    op.drop_table("leg_estimate")
    op.drop_index("ix_job_run_name_started", table_name="job_run")
    op.drop_table("job_run")
    op.drop_table("inbound_event")
    op.drop_table("hub")
    op.drop_index("ix_fleet_exception_date", table_name="fleet_exception")
    op.drop_table("fleet_exception")
    op.drop_index("ix_factor_lookup", table_name="factor_table")
    op.drop_table("factor_table")
    op.drop_index("ix_eta_log_source_computed", table_name="eta_log")
    op.drop_index("ix_eta_log_date", table_name="eta_log")
    op.drop_table("eta_log")
    op.drop_table("app_user")
