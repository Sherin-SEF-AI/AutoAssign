"""Generates contracts/datasource.openapi.yaml: the API the BluRabbit backends expose to HttpSource.

The schemas come from the same Pydantic records HttpSource validates against, so the contract
and the adapter cannot drift. CI regenerates the file and fails when it changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.adapters.base import (
    DriverRecord,
    HubRecord,
    PingRecord,
    SocCheckinRecord,
    TripEvent,
    TripRecord,
    VehicleRecord,
)

MODELS: tuple[type[BaseModel], ...] = (
    TripRecord,
    DriverRecord,
    VehicleRecord,
    HubRecord,
    SocCheckinRecord,
    PingRecord,
    TripEvent,
)

# (upstream, path, record, query parameters, description)
ENDPOINTS: tuple[tuple[str, str, str, tuple[tuple[str, str, bool], ...], str], ...] = (
    (
        "userapp",
        "/v1/trips",
        "TripRecord",
        (("service_date", "date", True),),
        "Every trip of the service date (IST), all channels in scope, any status.",
    ),
    (
        "userapp",
        "/v1/trip-events",
        "TripEvent",
        (("since", "date-time", True),),
        "Trip changes since a timestamp, oldest first: created, updated, cancelled, status.",
    ),
    (
        "driverapp",
        "/v1/drivers",
        "DriverRecord",
        (("service_date", "date", True),),
        "Roster for the service date: shifts in UTC, start and end locations, skills, approvals.",
    ),
    (
        "driverapp",
        "/v1/soc-checkins",
        "SocCheckinRecord",
        (("service_date", "date", True),),
        "Morning state of charge and odometer check-ins.",
    ),
    (
        "driverapp",
        "/v1/pings",
        "PingRecord",
        (("since", "date-time", True),),
        "Driver positions since a timestamp, oldest first.",
    ),
    ("admin", "/v1/vehicles", "VehicleRecord", (), "Fleet with current status (active, service, retired)."),
    ("admin", "/v1/hubs", "HubRecord", (), "Hubs used as depots and charging nodes."),
)


def schemas() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model in MODELS:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, sub in schema.pop("$defs", {}).items():
            out[name] = sub
        out[model.__name__] = schema
    out["Problem"] = {
        "type": "object",
        "required": ["status", "detail"],
        "properties": {
            "type": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "integer"},
            "detail": {"type": "string"},
            "code": {"type": "string"},
        },
    }
    return dict(sorted(out.items()))


def document() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for upstream, path, record, params, description in ENDPOINTS:
        parameters: list[dict[str, Any]] = [
            {"name": name, "in": "query", "required": required, "schema": {"type": "string", "format": fmt}}
            for name, fmt, required in params
        ]
        parameters += [
            {
                "name": "cursor",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": "Opaque cursor from the previous page's next_cursor.",
            },
            {
                "name": "limit",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 500},
            },
        ]
        paths[path] = {
            "get": {
                "operationId": f"{upstream}_{path.strip('/').replace('/', '_').replace('-', '_')}",
                "tags": [upstream],
                "summary": description,
                "x-upstream": upstream,
                "security": [{"ApiKey": []}],
                "parameters": parameters,
                "responses": {
                    "200": {
                        "description": "A page of records.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["items"],
                                    "properties": {
                                        "items": {
                                            "type": "array",
                                            "items": {"$ref": f"#/components/schemas/{record}"},
                                        },
                                        "next_cursor": {"type": ["string", "null"]},
                                    },
                                }
                            }
                        },
                    },
                    "401": {
                        "description": "Missing or invalid key.",
                        "content": {
                            "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                        },
                    },
                    "429": {"description": "Rate limited; the client retries with backoff."},
                    "5XX": {"description": "Server error; the client retries with backoff."},
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "BluRabbit DataSource contract",
            "version": "1.0.0",
            "description": (
                "Endpoints the BluRabbit User App, Driver App and Admin backends expose for "
                "blurabbit-dispatch HttpSource. Each upstream has its own base URL "
                "(UPSTREAM_USERAPP_URL, UPSTREAM_DRIVERAPP_URL, UPSTREAM_ADMIN_URL) and accepts the "
                "X-API-Key header (UPSTREAM_API_KEY). All timestamps carry an offset; service dates "
                "are IST calendar days. Lists are paginated with {items, next_cursor}."
            ),
        },
        "servers": [
            {"url": "{userapp}", "variables": {"userapp": {"default": "https://userapp.internal"}}},
            {"url": "{driverapp}", "variables": {"driverapp": {"default": "https://driverapp.internal"}}},
            {"url": "{admin}", "variables": {"admin": {"default": "https://admin.internal"}}},
        ],
        "paths": paths,
        "components": {
            "securitySchemes": {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
            "schemas": schemas(),
        },
    }


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return json.dumps(value)
    return json.dumps(str(value), ensure_ascii=False)


def to_yaml(value: Any, indent: int = 0) -> str:
    """Minimal YAML emitter for JSON compatible data (strings always quoted)."""
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            plain = str(k).replace("_", "").replace("-", "").isalnum() and not str(k)[0].isdigit()
            key = str(k) if plain else _scalar(k)
            if isinstance(v, dict | list) and v:
                lines.append(f"{pad}{key}:\n{to_yaml(v, indent + 1)}")
            else:
                lines.append(f"{pad}{key}: {to_yaml(v, 0) if isinstance(v, dict | list) else _scalar(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for item in value:
            if isinstance(item, dict | list) and item:
                body = to_yaml(item, indent + 1).lstrip()
                lines.append(f"{pad}- {body}")
            else:
                lines.append(f"{pad}- {to_yaml(item, 0) if isinstance(item, dict | list) else _scalar(item)}")
        return "\n".join(lines)
    return _scalar(value)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../contracts/datasource.openapi.yaml")
    text = (
        "# Generated by `python -m app.tools.contract`. Do not edit by hand.\n" + to_yaml(document()) + "\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
