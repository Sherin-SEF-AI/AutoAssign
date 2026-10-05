"""Runtime setting overrides edited through PUT /settings, merged over the environment."""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import Unprocessable
from app.db.models import SettingAudit, SettingOverride

EDITABLE: dict[str, str] = {
    "buffer_fixed_s": "buffers",
    "buffer_pct": "buffers",
    "buffer_min_s": "buffers",
    "early_arrival_s": "buffers",
    "range_caps_json": "caps",
    "e_roll_json": "caps",
    "here_routing_daily_cap": "caps",
    "here_matrix_daily_cap": "caps",
    "here_matrix_elements_daily_cap": "caps",
    "max_trips_per_driver": "caps",
    "max_gap_s": "thresholds",
    "max_deadhead_km": "thresholds",
    "risk_threshold_s": "thresholds",
    "late_tolerance_s": "thresholds",
    "drop_penalty_s": "thresholds",
    "solver_time_limit_s": "thresholds",
    "incremental_time_limit_s": "thresholds",
    "solver_solution_limit": "thresholds",
    "deadhead_m_weight": "thresholds",
    "p_aux_w": "thresholds",
    "here_enabled": "providers",
    "osrm_enabled": "providers",
}


def settings_view(s: Settings) -> dict[str, Any]:
    data = s.model_dump(mode="json")
    return {key: data[key] for key in EDITABLE}


def merge(base: Settings, overrides: dict[str, Any]) -> Settings:
    data = base.model_dump()
    data.update(overrides)
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"]) or "settings", "message": e["msg"]}
            for e in exc.errors()
        ]
        raise Unprocessable("invalid settings", code="invalid_settings", errors=errors) from exc


async def load_overrides(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(SettingOverride))).scalars()
    return {r.key: r.value for r in rows if r.key in EDITABLE}


async def save_overrides(
    session: AsyncSession, base: Settings, changes: dict[str, Any], actor: str
) -> Settings:
    unknown = sorted(set(changes) - set(EDITABLE))
    if unknown:
        raise Unprocessable(
            "settings not editable at runtime",
            code="invalid_settings",
            errors=[{"field": k, "message": "not editable"} for k in unknown],
        )
    current = await load_overrides(session)
    merged = merge(base, {**current, **changes})
    normalised = settings_view(merged)
    for key in changes:
        stmt = insert(SettingOverride).values(key=key, value=normalised[key], updated_by=actor)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SettingOverride.key],
            set_={"value": stmt.excluded.value, "updated_by": actor, "updated_at": stmt.excluded.updated_at},
        )
        await session.execute(stmt)
    session.add(SettingAudit(actor=actor, changes={k: normalised[k] for k in changes}))
    return merged


class SettingsCache:
    """Process local cache of the effective settings with a short TTL."""

    def __init__(self, base: Settings, ttl_s: float = 10.0):
        self.base = base
        self.ttl_s = ttl_s
        self._value: Settings | None = None
        self._at = 0.0

    def invalidate(self) -> None:
        self._value = None

    async def get(self, session: AsyncSession) -> Settings:
        now = time.monotonic()
        if self._value is not None and now - self._at < self.ttl_s:
            return self._value
        self._value = merge(self.base, await load_overrides(session))
        self._at = now
        return self._value
