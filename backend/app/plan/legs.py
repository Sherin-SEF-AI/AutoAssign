"""LegBook: legs for arbitrary sequences, from a graph first and the estimator on demand."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from itertools import pairwise

from app.config import Settings
from app.core.timeutil import day_start_utc
from app.eta.estimator import LegEstimator
from app.eta.types import LegRequest
from app.graph.build import buffer_s
from app.graph.model import Edge, Graph, HubLeg
from app.solver.evaluate import RouteEval, evaluate_route


class LegBook:
    def __init__(self, graph: Graph, settings: Settings, estimator: LegEstimator, service_date: date):
        self.g = graph
        self.settings = settings
        self.estimator = estimator
        self.day0 = day_start_utc(service_date)
        self.edges: dict[tuple[int, int], Edge] = dict(graph.edges)
        self.starts: dict[tuple[int, int], HubLeg] = dict(graph.start_legs)
        self.ends: dict[tuple[int, int], HubLeg] = dict(graph.end_legs)

    async def ensure(self, v_idx: int, seq: Sequence[int]) -> None:
        """Estimate every leg the sequence needs that the graph does not already hold."""
        nodes, v = self.g.nodes, self.g.vehicles[v_idx]
        policy = self.g.buffer_policy
        need_edges = [(a, b) for a, b in pairwise(seq) if (a, b) not in self.edges]
        if need_edges:
            ests = await self.estimator.estimate_many(
                [
                    LegRequest(nodes[a].drop, nodes[b].pickup, self.day0 + timedelta(seconds=nodes[a].end_s))
                    for a, b in need_edges
                ],
                "deadhead",
            )
            for (a, b), e in zip(need_edges, ests, strict=True):
                p50, p80, p90, m = e.as_ints()
                na = nodes[a]
                buf = buffer_s(
                    self.settings,
                    policy,
                    trip_p50=na.trip_p50_s,
                    trip_p80=na.trip_p80_s,
                    trip_p90=na.trip_p90_s,
                    leg_p50=p50,
                    leg_p80=p80,
                    leg_p90=p90,
                )
                self.edges[(a, b)] = Edge(p50, p80, p90, m, e.source, buf)
        if seq and (v_idx, seq[0]) not in self.starts:
            first = nodes[seq[0]]
            e = await self.estimator.estimate(
                v.origin, first.pickup, self.day0 + timedelta(seconds=v.origin_s), "hub"
            )
            p50, p80, p90, m = e.as_ints()
            buf = buffer_s(
                self.settings,
                policy,
                trip_p50=0,
                trip_p80=0,
                trip_p90=0,
                leg_p50=p50,
                leg_p80=p80,
                leg_p90=p90,
            )
            self.starts[(v_idx, seq[0])] = HubLeg(p50, p80, p90, m, e.source, buf)
        if seq and (seq[-1], v_idx) not in self.ends:
            last = nodes[seq[-1]]
            e = await self.estimator.estimate(
                last.drop, v.end, self.day0 + timedelta(seconds=last.end_s), "hub"
            )
            p50, p80, p90, m = e.as_ints()
            self.ends[(seq[-1], v_idx)] = HubLeg(p50, p80, p90, m, e.source, 0)

    def evaluate(self, v_idx: int, seq: Sequence[int]) -> RouteEval:
        return evaluate_route(
            self.settings,
            self.g.nodes,
            self.g.vehicles,
            v_idx,
            seq,
            edge=lambda a, b: self.edges.get((a, b)),
            start_leg=lambda v, j: self.starts.get((v, j)),
            end_leg=lambda i, v: self.ends.get((i, v)),
        )

    async def evaluate_many(self, routes: Iterable[tuple[int, Sequence[int]]]) -> dict[int, RouteEval]:
        out: dict[int, RouteEval] = {}
        for v_idx, seq in routes:
            await self.ensure(v_idx, seq)
            out[v_idx] = self.evaluate(v_idx, seq)
        return out
