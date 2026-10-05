from __future__ import annotations

import json

import pytest

from app.config import Settings, get_settings

BASE = {
    "database_url": "postgresql+asyncpg://u@h/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "x" * 40,
    "admin_email": "a@b.c",
    "admin_password": "password1",
}


def test_defaults_match_spec() -> None:
    s = Settings(**BASE)  # type: ignore[arg-type]
    assert s.here_routing_daily_cap == 600
    assert s.here_matrix_daily_cap == 40
    assert s.here_matrix_elements_daily_cap == 2000
    assert s.buffer_fixed_s == 900 and s.buffer_pct == 0.10 and s.buffer_min_s == 300
    assert s.max_gap_s == 14400 and s.max_deadhead_km == 25
    assert s.range_caps_json == {"tigor_ev": 140.0, "ec3": 165.0, "windsor": 220.0, "zs_ev": 245.0}
    assert s.speed_profile_json[2].kmh == 15.0


def test_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError, match="postgresql\\+asyncpg"):
        Settings(**{**BASE, "database_url": "postgres://x"})  # type: ignore[arg-type]


def test_caps_json_requires_all_models() -> None:
    with pytest.raises(ValueError, match="missing models"):
        Settings(**{**BASE, "range_caps_json": json.dumps({"tigor_ev": 100})})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not valid JSON"):
        Settings(**{**BASE, "range_caps_json": "{nope"})  # type: ignore[arg-type]


def test_http_source_requires_upstreams() -> None:
    with pytest.raises(ValueError, match="UPSTREAM_USERAPP_URL"):
        Settings(**{**BASE, "data_source": "http"})  # type: ignore[arg-type]


def test_service_keys_split() -> None:
    s = Settings(**{**BASE, "service_api_keys": "a, b ,,c"})  # type: ignore[arg-type]
    assert s.service_api_keys == ["a", "b", "c"]


def test_webhook_needs_secret() -> None:
    with pytest.raises(ValueError, match="NOTIFY_WEBHOOK_SECRET"):
        Settings(**{**BASE, "notify_webhook_url": "https://hook"})  # type: ignore[arg-type]


def test_get_settings_exits_on_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr("app.config.Settings.model_config", {**Settings.model_config, "env_file": None})
    get_settings.cache_clear()
    with pytest.raises(SystemExit):
        get_settings()
    assert "JWT_SECRET" in capsys.readouterr().err
    get_settings.cache_clear()
