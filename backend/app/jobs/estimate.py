"""estimate_day: estimate every trip leg, candidate deadhead edge and hub leg for a date."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.eta.factory import build_estimator
from app.graph.build import build_graph, plannable_trips, trip_nodes, vehicle_contexts
from app.jobs.context import AppContext
from app.plan.inputs import load_inputs


async def estimate_day(ctx: AppContext, service_date: date) -> dict[str, Any]:
    settings = await ctx.settings()
    async with ctx.factory() as session:
        inputs = await load_inputs(session, service_date)
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http)
    trips = plannable_trips(inputs.trips)
    nodes = await trip_nodes(service_date, trips, settings, estimator)
    vehicles = vehicle_contexts(service_date, inputs.drivers, inputs.vehicles, settings)
    graph = await build_graph(service_date, nodes, vehicles, settings, estimator)
    return {
        "service_date": service_date.isoformat(),
        "snapshot_id": str(inputs.snapshot_id),
        "graph": graph.stats,
        "estimator": estimator.stats.as_dict(),
    }
