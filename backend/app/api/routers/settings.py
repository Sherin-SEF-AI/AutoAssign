"""Runtime settings with validation and audit, and the active factor table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import Admin, Ctx, EffectiveSettings, Session, User
from app.db.models import SettingAudit
from app.db.settings_store import EDITABLE, save_overrides, settings_view
from app.eta.factors import load_factor_table

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    values: dict[str, Any]
    groups: dict[str, str]


class AuditOut(BaseModel):
    actor: str
    changes: dict[str, Any]
    created_at: datetime


@router.get("", response_model=SettingsOut)
async def get_settings_route(settings: EffectiveSettings, _: User) -> SettingsOut:
    return SettingsOut(values=settings_view(settings), groups=dict(EDITABLE))


@router.put("", response_model=SettingsOut)
async def put_settings(body: dict[str, Any], session: Session, ctx: Ctx, admin: Admin) -> SettingsOut:
    merged = await save_overrides(session, ctx.base_settings, body, admin.subject)
    ctx.settings_cache.invalidate()
    return SettingsOut(values=settings_view(merged), groups=dict(EDITABLE))


@router.get("/audit", response_model=list[AuditOut])
async def audit(session: Session, _: User) -> list[AuditOut]:
    rows = (
        await session.execute(select(SettingAudit).order_by(SettingAudit.created_at.desc()).limit(100))
    ).scalars()
    return [AuditOut(actor=r.actor, changes=r.changes, created_at=r.created_at) for r in rows]


class FactorOut(BaseModel):
    basis: str
    level: str
    time_bin: int | None
    weekday_type: str | None
    zone_cluster: int | None
    r50: float
    r80: float
    r90: float
    n: int


class ClusterOut(BaseModel):
    cluster_id: int
    lat: float
    lng: float


class FactorsOut(BaseModel):
    calibrated: bool
    calibration_id: str | None
    buffer_policy: str
    time_bins: list[dict[str, Any]]
    rows: list[FactorOut]
    defaults: list[FactorOut]
    clusters: list[ClusterOut]


@router.get("/factors", response_model=FactorsOut)
async def factors(session: Session, settings: EffectiveSettings, _: User) -> FactorsOut:
    table = await load_factor_table(session, settings)
    rows = [
        FactorOut(
            basis=k[0],
            level=f.level,
            time_bin=k[1],
            weekday_type=k[2],
            zone_cluster=k[3],
            r50=f.r50,
            r80=f.r80,
            r90=f.r90,
            n=f.n,
        )
        for k, f in sorted(
            table.rows.items(), key=lambda kv: tuple("" if x is None else str(x) for x in kv[0])
        )
    ]
    defaults = [
        FactorOut(
            basis=basis,
            level="default",
            time_bin=tb,
            weekday_type=None,
            zone_cluster=None,
            r50=f.r50,
            r80=f.r80,
            r90=f.r90,
            n=0,
        )
        for basis, bins in sorted(table.defaults.items())
        for tb, f in sorted(bins.items())
    ]
    return FactorsOut(
        calibrated=table.calibrated,
        calibration_id=table.calibration_id,
        buffer_policy="quantile" if table.calibrated else "fixed",
        time_bins=[b.model_dump() for b in settings.speed_profile_json],
        rows=rows,
        defaults=defaults,
        clusters=[ClusterOut(cluster_id=c, lat=p.lat, lng=p.lng) for c, p in table.centroids],
    )
