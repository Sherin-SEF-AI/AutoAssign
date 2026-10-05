"""Trips for a service date with plan overlay, estimate history and actuals."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import Session, User
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas import EstimateOut, EtaLogOut, TripDetailOut, TripOut
from app.core.errors import NotFound
from app.core.geo import Point
from app.db.models import EtaLog, LegEstimate, TripSnapshot
from app.plan.readmodels import trip_rows_for_date

router = APIRouter(prefix="/trips", tags=["trips"])


@router.get("", response_model=Page[TripOut])
async def list_trips(
    session: Session,
    _: User,
    date_: Annotated[date, Query(alias="date")],
    page: Annotated[PageParams, Depends(page_params)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
) -> Page[TripOut]:
    rows = await trip_rows_for_date(session, date_)
    if status:
        rows = [r for r in rows if r.status == status]
    if q:
        needle = q.lower()
        rows = [
            r
            for r in rows
            if needle in str(r.trip_id)
            or needle in r.pickup_address.lower()
            or needle in r.drop_address.lower()
            or needle in r.trip_type
            or needle in r.channel
            or (r.driver_name and needle in r.driver_name.lower())
            or (r.account_id and needle in r.account_id.lower())
        ]
    return paginate(rows, page)


@router.get("/{trip_id}", response_model=TripDetailOut)
async def get_trip(trip_id: uuid.UUID, session: Session, _: User) -> TripDetailOut:
    snap_row = (
        await session.execute(
            select(TripSnapshot)
            .where(TripSnapshot.trip_id == trip_id)
            .order_by(TripSnapshot.snapshot_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap_row is None:
        raise NotFound(f"trip {trip_id} not found", code="trip_not_found")
    rows = await trip_rows_for_date(session, snap_row.service_date)
    trip = next((r for r in rows if r.trip_id == trip_id), None)
    if trip is None:
        trip = TripOut.model_validate(snap_row)
    o = Point(snap_row.pickup_lat, snap_row.pickup_lng).h3()
    d = Point(snap_row.drop_lat, snap_row.drop_lng).h3()
    estimates = (
        await session.execute(
            select(LegEstimate)
            .where(
                LegEstimate.kind == "trip",
                LegEstimate.origin_h3 == o,
                LegEstimate.dest_h3 == d,
                LegEstimate.service_date == snap_row.service_date,
            )
            .order_by(LegEstimate.created_at.desc())
        )
    ).scalars()
    logs = (
        await session.execute(
            select(EtaLog).where(EtaLog.trip_id == trip_id).order_by(EtaLog.computed_at.desc())
        )
    ).scalars()
    return TripDetailOut(
        trip=trip,
        lifecycle=dict(snap_row.lifecycle),
        estimates=[EstimateOut.model_validate(e) for e in estimates],
        eta_log=[EtaLogOut.model_validate(e) for e in logs],
        snapshot_id=snap_row.snapshot_id,
    )
