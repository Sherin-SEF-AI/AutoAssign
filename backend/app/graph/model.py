"""Planning graph value types. Times are seconds from 00:00 IST of the service date."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.geo import Point

PLANNABLE_STATUSES = ("booked", "assigned")


@dataclass(frozen=True, slots=True)
class TripNode:
    trip_id: uuid.UUID
    pickup: Point
    drop: Point
    pickup_s: int
    service_s: int  # trip p80, or package duration
    trip_p50_s: int
    trip_p80_s: int
    trip_p90_s: int
    trip_m: int
    trip_source: str
    trip_type: str
    vehicle_class: str
    account_id: str | None
    vip: bool = False

    @property
    def needs_airport_skill(self) -> bool:
        return self.trip_type == "airport"

    @property
    def end_s(self) -> int:
        return self.pickup_s + self.service_s


@dataclass(frozen=True, slots=True)
class VehicleCtx:
    driver_id: uuid.UUID
    vehicle_id: uuid.UUID
    driver_name: str
    model: str
    vehicle_class: str
    start: Point
    end: Point
    shift_start_s: int
    shift_end_s: int
    max_hours: float
    skills: tuple[str, ...]
    approved_accounts: tuple[str, ...]
    e_roll_wh_per_km: float
    planning_cap_km: float
    soc_fraction: float = 1.0
    energy_used_wh: int = 0  # already consumed before the planning horizon (intraday)
    available_from_s: int | None = None  # intraday: when the driver becomes free
    available_at: Point | None = None  # intraday: where the driver becomes free

    @property
    def energy_cap_wh(self) -> int:
        return int(self.planning_cap_km * self.e_roll_wh_per_km * self.soc_fraction)

    @property
    def origin(self) -> Point:
        return self.available_at or self.start

    @property
    def origin_s(self) -> int:
        return self.shift_start_s if self.available_from_s is None else max(self.shift_start_s, self.available_from_s)

    def eligible(self, node: TripNode) -> bool:
        if node.vehicle_class == "suv" and self.vehicle_class != "suv":
            return False
        if node.needs_airport_skill and "airport" not in self.skills:
            return False
        return not (node.account_id and node.account_id not in self.approved_accounts)


@dataclass(frozen=True, slots=True)
class Edge:
    deadhead_p50_s: int
    deadhead_p80_s: int
    deadhead_p90_s: int
    deadhead_m: int
    source: str
    buffer_s: int

    def transit_s(self, from_node: TripNode) -> int:
        return from_node.service_s + self.deadhead_p80_s + self.buffer_s


@dataclass(frozen=True, slots=True)
class HubLeg:
    p50_s: int
    p80_s: int
    p90_s: int
    m: int
    source: str
    buffer_s: int


@dataclass(slots=True)
class Graph:
    service_date: date
    nodes: list[TripNode]
    vehicles: list[VehicleCtx]
    edges: dict[tuple[int, int], Edge] = field(default_factory=dict)
    start_legs: dict[tuple[int, int], HubLeg] = field(default_factory=dict)  # (vehicle, node)
    end_legs: dict[tuple[int, int], HubLeg] = field(default_factory=dict)  # (node, vehicle)
    buffer_policy: str = "fixed"
    stats: dict[str, Any] = field(default_factory=dict)
    sources: Counter[str] = field(default_factory=Counter)

    def node_index(self) -> dict[uuid.UUID, int]:
        return {n.trip_id: i for i, n in enumerate(self.nodes)}

    def vehicle_index(self) -> dict[uuid.UUID, int]:
        return {v.driver_id: i for i, v in enumerate(self.vehicles)}
