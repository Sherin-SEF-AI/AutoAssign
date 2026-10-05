"""Application configuration.

Every setting comes from the environment (optionally a .env file in development).
The process refuses to start when a value is missing or malformed and reports which one.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

VEHICLE_MODELS = ("tigor_ev", "ec3", "windsor", "zs_ev")

DEFAULT_RANGE_CAPS = {"tigor_ev": 140.0, "ec3": 165.0, "windsor": 220.0, "zs_ev": 245.0}
DEFAULT_E_ROLL = {"tigor_ev": 137.0, "ec3": 128.0, "windsor": 123.0, "zs_ev": 149.0}
DEFAULT_SPEED_PROFILE = [
    {"start_min": 0, "kmh": 38.0},
    {"start_min": 360, "kmh": 28.0},
    {"start_min": 420, "kmh": 15.0},
    {"start_min": 600, "kmh": 22.0},
    {"start_min": 1020, "kmh": 13.0},
    {"start_min": 1260, "kmh": 30.0},
]


class SpeedBin(BaseModel):
    start_min: int = Field(ge=0, lt=1440)
    kmh: float = Field(gt=0)


def _json_dict(raw: Any, name: str) -> dict[str, float]:
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object keyed by vehicle model")
    missing = [m for m in VEHICLE_MODELS if m not in data]
    if missing:
        raise ValueError(f"{name} is missing models: {', '.join(missing)}")
    out: dict[str, float] = {}
    for key, value in data.items():
        num = float(value)
        if num <= 0:
            raise ValueError(f"{name}[{key}] must be positive")
        out[str(key)] = num
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # Infrastructure
    database_url: str = Field(description="postgresql+asyncpg://user:pass@host/db")
    redis_url: str = Field(description="redis://host:6379/0")
    jwt_secret: str = Field(min_length=32)
    jwt_ttl_hours: int = Field(default=12, ge=1, le=72)
    admin_email: str = Field(min_length=3)
    admin_password: str = Field(min_length=8)
    service_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    web_origin: str = "http://localhost:5173"
    environment: Literal["development", "test", "production"] = "development"

    # Data source
    data_source: Literal["synthetic", "http"] = "synthetic"
    upstream_userapp_url: str | None = None
    upstream_driverapp_url: str | None = None
    upstream_admin_url: str | None = None
    upstream_api_key: str | None = None
    upstream_timeout_s: float = Field(default=10.0, gt=0)
    synthetic_seed: int = 42

    # HERE
    here_api_key: str | None = None
    here_routing_url: str = "https://router.hereapi.com/v8/routes"
    here_matrix_url: str = "https://matrix.router.hereapi.com/v8/matrix"
    here_tiles_url: str = "https://vector.hereapi.com/v2/vectortiles/base/mc"
    here_routing_daily_cap: int = Field(default=600, ge=0)
    here_matrix_daily_cap: int = Field(default=40, ge=0)
    here_matrix_elements_daily_cap: int = Field(default=2000, ge=0)
    here_enabled: bool = True
    here_retention_days: int = Field(default=30, ge=1, le=30)

    # OSRM
    osrm_url: str | None = None
    osrm_enabled: bool = True

    # HTTP hygiene for providers
    http_timeout_s: float = Field(default=5.0, gt=0)
    http_retries: int = Field(default=3, ge=0, le=10)
    http_backoff_base_s: float = Field(default=0.5, ge=0)
    breaker_failure_threshold: int = Field(default=5, ge=1)
    breaker_open_s: int = Field(default=600, ge=1)

    # ETA
    cache_fresh_s: int = Field(default=86400, ge=60)
    hot_cache_ttl_s: int = Field(default=6 * 3600, ge=60)
    factor_r50_default: float = Field(default=1.00, gt=0)
    factor_r80_default: float = Field(default=1.15, gt=0)
    factor_r90_default: float = Field(default=1.30, gt=0)
    circuity: float = Field(default=1.35, ge=1.0)
    speed_profile_json: Annotated[list[SpeedBin], NoDecode] = Field(
        default_factory=lambda: [SpeedBin(**b) for b in DEFAULT_SPEED_PROFILE]
    )
    calibration_min_n: int = Field(default=30, ge=1)
    zone_clusters_k: int = Field(default=8, ge=1)

    # Solver
    solver_time_limit_s: int = Field(default=60, ge=1)
    incremental_time_limit_s: int = Field(default=20, ge=1)
    solver_solution_limit: int = Field(default=3000, ge=1)
    incremental_solution_limit: int = Field(default=1000, ge=1)
    drop_penalty_s: int = Field(default=100000, ge=1)
    buffer_fixed_s: int = Field(default=900, ge=0)
    buffer_pct: float = Field(default=0.10, ge=0, le=1)
    buffer_min_s: int = Field(default=300, ge=0)
    early_arrival_s: int = Field(default=600, ge=0)
    max_gap_s: int = Field(default=14400, ge=600)
    max_deadhead_km: float = Field(default=25.0, gt=0)
    max_trips_per_driver: int = Field(default=5, ge=1)
    fairness_cost_s: int = Field(default=1800, ge=0)
    deadhead_m_weight: float = Field(default=0.02, ge=0)
    p_aux_w: float = Field(default=1200.0, ge=0)
    range_caps_json: Annotated[dict[str, float], NoDecode] = Field(
        default_factory=lambda: dict(DEFAULT_RANGE_CAPS)
    )
    e_roll_json: Annotated[dict[str, float], NoDecode] = Field(default_factory=lambda: dict(DEFAULT_E_ROLL))
    handling_s: int = Field(default=240, ge=0)
    package_km_per_hour: float = Field(default=12.0, ge=0)

    # Monitoring and repair
    risk_threshold_s: int = Field(default=600, ge=0)
    late_tolerance_s: int = Field(default=300, ge=0)
    monitor_interval_s: int = Field(default=300, ge=10)
    float_horizon_s: int = Field(default=5400, ge=0)
    absent_grace_s: int = Field(default=900, ge=0)

    # Notifications
    notify_webhook_url: str | None = None
    notify_webhook_secret: str | None = None

    # Simulator
    sim_date: str | None = None
    sim_speed: float = Field(default=60.0, gt=0, le=10000)
    sim_ping_interval_s: int = Field(default=30, ge=1)

    # Misc
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    tz_display: str = "Asia/Kolkata"
    pings_max_batch: int = Field(default=5000, ge=1)
    pings_max_body_bytes: int = Field(default=2_000_000, ge=1024)
    login_rate_per_minute: int = Field(default=5, ge=1)

    @field_validator("service_api_keys", mode="before")
    @classmethod
    def _split_keys(cls, v: Any) -> list[str]:
        if v is None or v == "":
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [part.strip() for part in str(v).split(",") if part.strip()]

    @field_validator("range_caps_json", mode="before")
    @classmethod
    def _caps(cls, v: Any) -> dict[str, float]:
        return _json_dict(v, "RANGE_CAPS_JSON")

    @field_validator("e_roll_json", mode="before")
    @classmethod
    def _eroll(cls, v: Any) -> dict[str, float]:
        return _json_dict(v, "E_ROLL_JSON")

    @field_validator("speed_profile_json", mode="before")
    @classmethod
    def _speed(cls, v: Any) -> list[Any]:
        data = v
        if isinstance(v, str):
            try:
                data = json.loads(v)
            except json.JSONDecodeError as exc:
                raise ValueError(f"SPEED_PROFILE_JSON is not valid JSON: {exc}") from exc
        if not isinstance(data, list) or not data:
            raise ValueError("SPEED_PROFILE_JSON must be a non-empty list")
        return data

    @field_validator("database_url")
    @classmethod
    def _db(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// scheme")
        return v

    @field_validator("redis_url")
    @classmethod
    def _redis(cls, v: str) -> str:
        if not v.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must start with redis:// or rediss://")
        return v

    @model_validator(mode="after")
    def _cross(self) -> Settings:
        starts = [b.start_min for b in self.speed_profile_json]
        if starts[0] != 0 or starts != sorted(set(starts)):
            raise ValueError("SPEED_PROFILE_JSON bins must start at 0 and be strictly increasing")
        if not (self.factor_r50_default <= self.factor_r80_default <= self.factor_r90_default):
            raise ValueError("factor defaults must satisfy r50 <= r80 <= r90")
        if self.data_source == "http":
            missing = [
                name
                for name, val in (
                    ("UPSTREAM_USERAPP_URL", self.upstream_userapp_url),
                    ("UPSTREAM_DRIVERAPP_URL", self.upstream_driverapp_url),
                    ("UPSTREAM_ADMIN_URL", self.upstream_admin_url),
                    ("UPSTREAM_API_KEY", self.upstream_api_key),
                )
                if not val
            ]
            if missing:
                raise ValueError(f"DATA_SOURCE=http requires {', '.join(missing)}")
        if self.notify_webhook_url and not self.notify_webhook_secret:
            raise ValueError("NOTIFY_WEBHOOK_URL requires NOTIFY_WEBHOOK_SECRET")
        if self.environment == "production" and self.jwt_secret.startswith("change-me"):
            raise ValueError("JWT_SECRET must be changed in production")
        return self

    @property
    def here_available(self) -> bool:
        return bool(self.here_enabled and self.here_api_key)

    @property
    def osrm_available(self) -> bool:
        return bool(self.osrm_enabled and self.osrm_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg,unused-ignore]
    except ValidationError as exc:
        lines = ["Invalid configuration, refusing to start:"]
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]).upper() or "SETTINGS"
            lines.append(f"  {loc}: {err['msg']}")
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(2) from exc
