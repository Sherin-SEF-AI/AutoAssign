"""HERE vector tile proxy and MapLibre style.

The HERE key is injected server side; the browser only ever sees /api/v1/map/tiles/... URLs.
Routing results are drawn exclusively on this HERE basemap, with HERE attribution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Path, Response

from app.api.deps import Ctx, EffectiveSettings, User
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.metrics import PROVIDER_ERRORS

router = APIRouter(prefix="/map", tags=["map"])
log = get_logger("api.map")


class HereUnavailable(AppError):
    status = 503
    code = "here_unavailable"


def attribution() -> str:
    return f"© {datetime.now(UTC).year} HERE"


@router.get("/style.json")
async def style(settings: EffectiveSettings, _: User) -> dict[str, Any]:
    """MapLibre style for HERE OMV tiles. The web client appends its token to tile requests."""
    roads = [
        ("highway", "#f2b84b", 3.0),
        ("major_road", "#ffffff", 2.2),
        ("minor_road", "#ffffff", 1.0),
    ]
    layers: list[dict[str, Any]] = [
        {"id": "background", "type": "background", "paint": {"background-color": "#eef0f2"}},
        {
            "id": "landuse",
            "type": "fill",
            "source": "here",
            "source-layer": "landuse",
            "paint": {"fill-color": "#e2ead9", "fill-opacity": 0.7},
        },
        {
            "id": "water",
            "type": "fill",
            "source": "here",
            "source-layer": "water",
            "paint": {"fill-color": "#b9d3e6"},
        },
        {
            "id": "buildings",
            "type": "fill",
            "source": "here",
            "source-layer": "buildings",
            "minzoom": 14,
            "paint": {"fill-color": "#dcdcdc"},
        },
    ]
    for kind, color, width in roads:
        layers.append(
            {
                "id": f"roads-{kind}",
                "type": "line",
                "source": "here",
                "source-layer": "roads",
                "filter": ["==", ["get", "kind"], kind],
                "paint": {"line-color": color, "line-width": width},
            }
        )
    return {
        "version": 8,
        "name": "HERE base",
        "sources": {
            "here": {
                "type": "vector",
                "tiles": ["/api/v1/map/tiles/{z}/{x}/{y}.omv"],
                "minzoom": 1,
                "maxzoom": 17,
                "attribution": attribution(),
            }
        },
        "layers": layers,
        "metadata": {"here_enabled": settings.here_available, "attribution": attribution()},
    }


@router.get("/tiles/{z}/{x}/{y}.omv")
async def tile(
    ctx: Ctx,
    settings: EffectiveSettings,
    _: User,
    z: int = Path(ge=0, le=20),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
) -> Response:
    if not settings.here_available or not settings.here_api_key:
        raise HereUnavailable("HERE is disabled or not configured")
    if x >= 2**z or y >= 2**z:
        raise AppError("tile out of range", code="tile_out_of_range", status=422)
    url = f"{settings.here_tiles_url.rstrip('/')}/{z}/{x}/{y}/omv"
    try:
        upstream = await ctx.http.get(
            url, params={"apikey": settings.here_api_key}, timeout=settings.http_timeout_s
        )
    except httpx.HTTPError as exc:
        PROVIDER_ERRORS.labels(provider="here_tiles", kind="transport").inc()
        log.warning("here_tile_failed", error=str(exc))
        raise HereUnavailable("HERE tile request failed") from exc
    if upstream.status_code == 204:
        return Response(status_code=204)
    if upstream.status_code != 200:
        PROVIDER_ERRORS.labels(provider="here_tiles", kind=f"http_{upstream.status_code}").inc()
        raise HereUnavailable(f"HERE tiles returned {upstream.status_code}")
    return Response(
        content=upstream.content,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "private, max-age=86400", "X-Attribution": attribution()},
    )
