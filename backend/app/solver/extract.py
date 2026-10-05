"""Turns a solver result into per driver routes and explains every dropped trip."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.graph.model import Graph
from app.solver.evaluate import RouteEval, evaluate_route, leg_energy_wh
from app.solver.model import SolveResult

REASONS = ("no_eligible_vehicle", "shift", "energy_cap", "time_window")


@dataclass(slots=True)
class Extraction:
    routes: dict[int, RouteEval]
    unassigned: dict[int, str]


def graph_lookups(graph: Graph):  # type: ignore[no-untyped-def]
    return (
        lambda i, j: graph.edges.get((i, j)),
        lambda v, j: graph.start_legs.get((v, j)),
        lambda i, v: graph.end_legs.get((i, v)),
    )


def unassigned_reason(graph: Graph, settings: Settings, node_idx: int) -> str:
    """Re-check a dropped node against every vehicle, most fundamental reason first."""
    node = graph.nodes[node_idx]
    eligible = [v for v, ctx in enumerate(graph.vehicles) if ctx.eligible(node)]
    if not eligible:
        return "no_eligible_vehicle"
    shift_ok = [v for v in eligible if (v, node_idx) in graph.start_legs and (node_idx, v) in graph.end_legs]
    if not shift_ok:
        return "shift"
    for v in shift_ok:
        ctx = graph.vehicles[v]
        s, e = graph.start_legs[(v, node_idx)], graph.end_legs[(node_idx, v)]
        need = leg_energy_wh(settings, ctx, s.m + node.trip_m, s.p80_s + node.service_s) + leg_energy_wh(
            settings, ctx, e.m, e.p80_s
        )
        if need <= ctx.energy_cap_wh - ctx.energy_used_wh:
            return "time_window"
    return "energy_cap"


def extract(graph: Graph, settings: Settings, result: SolveResult) -> Extraction:
    edge, start, end = graph_lookups(graph)
    routes = {
        v: evaluate_route(
            settings, graph.nodes, graph.vehicles, v, seq, edge=edge, start_leg=start, end_leg=end
        )
        for v, seq in result.routes.items()
    }
    for v, ev in routes.items():
        if ev.stops and result.energy_cumul.get(v) is not None and ev.energy_wh != result.energy_cumul[v]:
            raise AssertionError(
                f"energy mismatch for vehicle {v}: evaluated {ev.energy_wh}, solver {result.energy_cumul[v]}"
            )
    unassigned = {i: unassigned_reason(graph, settings, i) for i in result.dropped}
    return Extraction(routes=routes, unassigned=unassigned)
