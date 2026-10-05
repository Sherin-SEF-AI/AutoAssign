"""Recorded upstream fixtures for HttpSource: a recorder and a server that replays them.

record: writes what the configured DataSource returns, in contract shape, under a directory.
server: serves that directory with the contract's paths, X-API-Key check and cursor pagination.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse

from app.adapters.base import DataSource

LAYOUT = {
    "/v1/trips": ("userapp", "trips", True),
    "/v1/drivers": ("driverapp", "drivers", True),
    "/v1/soc-checkins": ("driverapp", "soc-checkins", True),
    "/v1/vehicles": ("admin", "vehicles", False),
    "/v1/hubs": ("admin", "hubs", False),
}


def _dump(items: Sequence[Any]) -> list[Any]:
    return [i.model_dump(mode="json") for i in items]


async def record(source: DataSource, out: Path, dates: Sequence[date]) -> dict[str, int]:
    counts: dict[str, int] = {}
    files: dict[Path, list[Any]] = {}
    for d in dates:
        files[out / "userapp" / "trips" / f"{d}.json"] = _dump(await source.list_trips(d))
        files[out / "driverapp" / "drivers" / f"{d}.json"] = _dump(await source.list_drivers(d))
        files[out / "driverapp" / "soc-checkins" / f"{d}.json"] = _dump(await source.list_soc_checkins(d))
    files[out / "admin" / "vehicles.json"] = _dump(await source.list_vehicles())
    files[out / "admin" / "hubs.json"] = _dump(await source.list_hubs())

    def write() -> None:
        for path, items in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(items, indent=1, sort_keys=True) + "\n")
            counts[str(path.relative_to(out))] = len(items)

    await asyncio.to_thread(write)
    return counts


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def create_fixture_app(root: Path, api_key: str, page_size: int = 50) -> FastAPI:
    app = FastAPI(title="Upstream fixture server")

    def page(items: list[Any], cursor: str | None) -> dict[str, Any]:
        offset = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
        nxt = offset + page_size
        return {"items": items[offset:nxt], "next_cursor": _cursor(nxt) if nxt < len(items) else None}

    def load(upstream: str, name: str, day: str | None) -> list[Any]:
        path = root / upstream / name / f"{day}.json" if day else root / upstream / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else []

    def unauthorized() -> JSONResponse:
        return JSONResponse(
            {"status": 401, "detail": "invalid key", "code": "unauthorized"},
            status_code=401,
            media_type="application/problem+json",
        )

    for path, (upstream, name, dated) in LAYOUT.items():

        def make(upstream: str = upstream, name: str = name, dated: bool = dated):  # type: ignore[no-untyped-def]
            async def handler(
                x_api_key: str | None = Header(default=None),
                service_date: str | None = Query(default=None),
                cursor: str | None = Query(default=None),
            ) -> Any:
                if x_api_key != api_key:
                    return unauthorized()
                return page(load(upstream, name, service_date if dated else None), cursor)

            return handler

        app.get(path)(make())

    @app.get("/v1/pings")
    async def pings(x_api_key: str | None = Header(default=None), since: str | None = None) -> Any:
        return unauthorized() if x_api_key != api_key else {"items": [], "next_cursor": None}

    @app.get("/v1/trip-events")
    async def events(x_api_key: str | None = Header(default=None), since: str | None = None) -> Any:
        return unauthorized() if x_api_key != api_key else {"items": [], "next_cursor": None}

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="fixture-server")
    parser.add_argument("--dir", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(create_fixture_app(Path(args.dir), args.key), host="0.0.0.0", port=args.port)  # noqa: S104


if __name__ == "__main__":
    main()
