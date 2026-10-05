"""Seeded synthetic world for BluRabbit dispatch.

Every value is a pure function of (seed, date, today). Random streams are derived from
string seeds so the output never depends on call order or process state.
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import cached_property
from zoneinfo import ZoneInfo

from app.adapters.base import (
    DriverRecord,
    HubRecord,
    SocCheckinRecord,
    TripRecord,
    VehicleRecord,
)
from app.adapters.synthetic import profiles as P
from app.adapters.synthetic.zones import (
    AIRPORT,
    CITY_ZONES,
    JITTER_CLIP_M,
    JITTER_SIGMA_M,
    RESIDENTIAL,
    TECH_PARKS,
    ZONES,
    Zone,
)

IST = ZoneInfo("Asia/Kolkata")
NAMESPACE = uuid.UUID("6b1f5d8e-3c39-4a51-9c0e-5a3cbbd1e0a7")
EARTH_R = 6_371_008.8

TRIPS_PER_DAY = 120
ETS_SERIES = 46
ETS_KEEP = 0.90
SHARE_AIRPORT, SHARE_CITY, SHARE_PACKAGE = 0.25, 0.30, 0.10
SUV_SHARE = 0.20
VIP_SHARE = 0.05
ACCOUNT_SHARE = 0.10
CANCEL_SHARE = 0.04
NO_SHOW_SHARE = 0.01
AIRPORT_SKILL_SHARE = 0.60
SERVICE_DAY_SHARE = 0.10
LOW_SOC_SHARE = 0.10
UBER_DAY_SHARE = 0.50


def _rng(*parts: object) -> random.Random:
    return random.Random(":".join(str(p) for p in parts))


def _uuid(*parts: object) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts))


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def _poisson(rng: random.Random, lam: float) -> int:
    # Knuth for moderate lambda, exact and deterministic for a given stream.
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _ist(d: date, minute: float) -> datetime:
    base = datetime.combine(d, time(0, 0), tzinfo=IST)
    return (base + timedelta(minutes=minute)).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Spot:
    lat: float
    lng: float
    address: str


def _jitter(rng: random.Random, zone: Zone) -> Spot:
    north, east = rng.gauss(0, JITTER_SIGMA_M), rng.gauss(0, JITTER_SIGMA_M)
    norm = math.hypot(north, east)
    if norm > JITTER_CLIP_M:
        north, east = north * JITTER_CLIP_M / norm, east * JITTER_CLIP_M / norm
    lat = zone.lat + north / 111_320.0
    lng = zone.lng + east / (111_320.0 * math.cos(math.radians(zone.lat)))
    street = rng.randint(1, 48)
    return Spot(round(lat, 6), round(lng, 6), f"{street} Main Road, {zone.name}, Bengaluru")


@dataclass(frozen=True, slots=True)
class Draft:
    key: str
    trip_type: str
    channel: str
    minute: float
    pickup: Spot
    drop: Spot
    tags: dict[str, object] = field(default_factory=dict)
    package_hours: float | None = None


@dataclass(frozen=True, slots=True)
class DriverProfile:
    driver_id: uuid.UUID
    index: int
    name: str
    home: Spot
    shift_pattern: tuple[int, int]
    airport_skill: bool
    vehicle_id: uuid.UUID
    approved_accounts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VehicleProfile:
    vehicle_id: uuid.UUID
    index: int
    registration: str
    model: P.FleetModel
    hub_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class EtsSeries:
    series_id: int
    client: str
    home: Spot
    office: Spot
    to_work: bool
    minute: int


class SyntheticWorld:
    """Deterministic generator. Distances can be supplied by an optional road router."""

    def __init__(self, seed: int):
        self.seed = seed

    # Static entities --------------------------------------------------------

    @cached_property
    def hubs(self) -> list[HubRecord]:
        return [
            HubRecord(
                hub_id=_uuid(self.seed, "hub", i),
                name=name,
                lat=lat,
                lng=lng,
                ac_kw=P.HUB_AC_KW,
                dc_kw=P.HUB_DC_KW,
                ac_points=P.HUB_AC_POINTS,
                dc_points=P.HUB_DC_POINTS,
            )
            for i, (name, lat, lng) in enumerate(P.HUBS)
        ]

    @cached_property
    def vehicle_profiles(self) -> list[VehicleProfile]:
        out: list[VehicleProfile] = []
        rng = _rng(self.seed, "vehicles")
        idx = 0
        for model in P.FLEET:
            for _ in range(model.count):
                letters = rng.choice("ABCDEFGHJKMN") + rng.choice("ABCDEFGHJKMN")
                reg = f"{model.reg_prefix}{letters}{rng.randint(1000, 9999)}"
                hub = self.hubs[idx % len(self.hubs)].hub_id
                out.append(VehicleProfile(_uuid(self.seed, "vehicle", idx), idx, reg, model, hub))
                idx += 1
        return out

    @cached_property
    def driver_profiles(self) -> list[DriverProfile]:
        rng = _rng(self.seed, "drivers")
        n = len(self.vehicle_profiles)
        skilled = set(rng.sample(range(n), round(n * AIRPORT_SKILL_SHARE)))
        approvals: dict[int, list[str]] = {i: [] for i in range(n)}
        for account in P.CORPORATE_ACCOUNTS:
            for i in rng.sample(range(n), 3):
                approvals[i].append(account)
        names: set[str] = set()
        out: list[DriverProfile] = []
        for i in range(n):
            while True:
                name = f"{rng.choice(P.FIRST_NAMES)} {rng.choice(P.LAST_NAMES)}"
                if name not in names:
                    names.add(name)
                    break
            home = _jitter(rng, rng.choice(RESIDENTIAL))
            out.append(
                DriverProfile(
                    driver_id=_uuid(self.seed, "driver", i),
                    index=i,
                    name=name,
                    home=home,
                    shift_pattern=P.SHIFT_PATTERNS_IST[i * len(P.SHIFT_PATTERNS_IST) // n],
                    airport_skill=i in skilled,
                    vehicle_id=self.vehicle_profiles[i].vehicle_id,
                    approved_accounts=tuple(sorted(approvals[i])),
                )
            )
        return out

    @cached_property
    def ets_series(self) -> list[EtsSeries]:
        rng = _rng(self.seed, "ets-series")
        out: list[EtsSeries] = []
        for s in range(ETS_SERIES):
            to_work = s % 2 == 0
            if to_work:
                minute = 7 * 60 + 15 * rng.randint(0, 12)
            else:
                minute = 17 * 60 + 30 + 15 * rng.randint(0, 12)
            out.append(
                EtsSeries(
                    series_id=s,
                    client=P.ETS_CLIENTS[s % len(P.ETS_CLIENTS)],
                    home=_jitter(rng, rng.choice(RESIDENTIAL)),
                    office=_jitter(rng, rng.choice(TECH_PARKS)),
                    to_work=to_work,
                    minute=minute,
                )
            )
        return out

    # Per day ----------------------------------------------------------------

    def is_rain_day(self, d: date) -> bool:
        return _rng(self.seed, d, "rain").random() < P.RAIN_DAY_SHARE

    def off_driver_indexes(self, d: date) -> set[int]:
        week = d.isocalendar().week
        return {p.index for p in self.driver_profiles if (p.index + week) % 7 == d.weekday()}

    def service_vehicle_index(self, d: date) -> int | None:
        rng = _rng(self.seed, d, "service")
        if rng.random() < SERVICE_DAY_SHARE:
            return rng.randrange(len(self.vehicle_profiles))
        return None

    def uber_driver_index(self, d: date) -> int | None:
        rng = _rng(self.seed, d, "uber")
        if rng.random() >= UBER_DAY_SHARE:
            return None
        on_duty = [p.index for p in self.driver_profiles if p.index not in self.off_driver_indexes(d)]
        return rng.choice(on_duty)

    def vehicles(self, d: date) -> list[VehicleRecord]:
        service_idx = self.service_vehicle_index(d)
        return [
            VehicleRecord(
                vehicle_id=v.vehicle_id,
                registration=v.registration,
                model=v.model.model,
                variant=v.model.variant,
                vehicle_class="suv" if v.model.vehicle_class == "suv" else "sedan",
                battery_kwh=v.model.battery_kwh,
                seats=v.model.seats,
                luggage_class=v.model.luggage_class,
                status="service" if v.index == service_idx else "active",
                hub_id=v.hub_id,
            )
            for v in self.vehicle_profiles
        ]

    def drivers(self, d: date) -> list[DriverRecord]:
        off = self.off_driver_indexes(d)
        uber = self.uber_driver_index(d)
        out: list[DriverRecord] = []
        for p in self.driver_profiles:
            on = p.index not in off
            start_h, end_h = p.shift_pattern
            out.append(
                DriverRecord(
                    driver_id=p.driver_id,
                    service_date=d,
                    name=p.name,
                    active=on,
                    shift_start_at=_ist(d, start_h * 60) if on else None,
                    shift_end_at=_ist(d, end_h * 60) if on else None,
                    start_lat=p.home.lat,
                    start_lng=p.home.lng,
                    end_lat=p.home.lat,
                    end_lng=p.home.lng,
                    vehicle_id=p.vehicle_id,
                    skills=["airport"] if p.airport_skill else [],
                    approved_accounts=list(p.approved_accounts),
                    channel_today="uber" if p.index == uber else "blurabbit",
                    max_hours=10.0,
                )
            )
        return out

    def soc_checkins(self, d: date) -> list[SocCheckinRecord]:
        off = self.off_driver_indexes(d)
        rng = _rng(self.seed, d, "soc")
        day_index = (d - date(2026, 1, 1)).days
        out: list[SocCheckinRecord] = []
        for p in self.driver_profiles:
            low = rng.random() < LOW_SOC_SHARE
            soc = round(rng.uniform(55.0, 85.0), 1) if low else 100.0
            odo = round(4000 + 140.0 * max(0, day_index) + 37.0 * p.index, 1)
            if p.index in off:
                continue
            out.append(
                SocCheckinRecord(
                    driver_id=p.driver_id,
                    vehicle_id=p.vehicle_id,
                    service_date=d,
                    soc_pct=soc,
                    odometer_km=odo,
                    reported_at=_ist(d, p.shift_pattern[0] * 60 - 20),
                )
            )
        return out

    def trips(self, d: date, today: date) -> list[TripRecord]:
        rng = _rng(self.seed, d, "trips")
        drafts: list[Draft] = []
        keep = _rng(self.seed, d, "ets-keep")
        for s in self.ets_series:
            if keep.random() >= ETS_KEEP:
                continue
            pickup, drop = (s.home, s.office) if s.to_work else (s.office, s.home)
            drafts.append(
                Draft(
                    key=f"ets:{s.series_id}",
                    trip_type="ets",
                    channel="ets",
                    minute=s.minute,
                    pickup=pickup,
                    drop=drop,
                    tags={"ets_client": s.client, "series_id": s.series_id},
                )
            )
        total = _poisson(rng, TRIPS_PER_DAY)
        others = max(0, total - len(drafts))
        weights = (SHARE_AIRPORT, SHARE_CITY, SHARE_PACKAGE)
        for i in range(others):
            kind = rng.choices(("airport", "city", "package"), weights=weights)[0]
            drafts.append(self._draft(rng, kind, i))
        out = [self._finalise(d, today, draft) for draft in drafts]
        out.sort(key=lambda t: (t.scheduled_pickup_at, str(t.trip_id)))
        return out

    def _draft(self, rng: random.Random, kind: str, i: int) -> Draft:
        if kind == "airport":
            morning = rng.random() < 0.5
            minute = rng.uniform(210, 420) if morning else rng.uniform(1080, 1380)
            city = _jitter(rng, rng.choice(CITY_ZONES))
            airport = _jitter(rng, AIRPORT)
            to_airport = rng.random() < 0.5
            pickup, drop = (city, airport) if to_airport else (airport, city)
            return Draft(f"airport:{i}", "airport", "blurabbit", minute, pickup, drop)
        if kind == "city":
            r = rng.random()
            if r < 0.35:
                minute = rng.gauss(540, 45)
            elif r < 0.70:
                minute = rng.gauss(1110, 45)
            else:
                minute = rng.uniform(480, 1320)
            minute = min(1320.0, max(480.0, minute))
            while True:
                a, b = rng.sample(CITY_ZONES, 2)
                pickup, drop = _jitter(rng, a), _jitter(rng, b)
                if _haversine_m(pickup.lat, pickup.lng, drop.lat, drop.lng) >= 2000:
                    break
            return Draft(f"city:{i}", "city", "blurabbit", minute, pickup, drop)
        hours = 4.0 if rng.random() < 0.6 else 8.0
        minute = 540 + 15 * rng.randint(0, 20)
        spot = _jitter(rng, rng.choice(CITY_ZONES))
        return Draft(f"package:{i}", "package", "blurabbit", minute, spot, spot, package_hours=hours)

    def _finalise(self, d: date, today: date, draft: Draft) -> TripRecord:
        key = draft.key
        rng = _rng(self.seed, d, "trip", key)
        minute = round(draft.minute / 5) * 5
        scheduled = _ist(d, minute)
        pickup, drop = draft.pickup, draft.drop
        tags: dict[str, object] = dict(draft.tags)
        trip_type = draft.trip_type
        vehicle_class = "suv" if rng.random() < SUV_SHARE else "any"
        if rng.random() < VIP_SHARE:
            tags["vip"] = True
        account_id = rng.choice(P.CORPORATE_ACCOUNTS) if rng.random() < ACCOUNT_SHARE else None
        pax = rng.randint(1, 4)
        luggage = rng.randint(1, 3) if trip_type == "airport" else rng.randint(0, 1)
        booked_at = scheduled - timedelta(hours=rng.uniform(6, 168))
        lifecycle: dict[str, datetime] = {"booked": booked_at}
        status = "booked"
        actual_pickup = actual_drop = None
        actual_distance = None
        roll = rng.random()
        past = d < today
        if roll < CANCEL_SHARE:
            status = "cancelled"
            lifecycle["cancelled"] = booked_at + (scheduled - booked_at) * rng.uniform(0.2, 0.9)
        elif past and roll < CANCEL_SHARE + NO_SHOW_SHARE:
            status = "no_show"
            lifecycle["assigned"] = _ist(d, -150)
            lifecycle["en_route"] = scheduled - timedelta(minutes=rng.uniform(20, 40))
            lifecycle["arrived"] = scheduled - timedelta(minutes=rng.uniform(0, 8))
            lifecycle["no_show"] = scheduled + timedelta(minutes=15)
        elif past:
            status = "completed"
            actual_pickup = scheduled + timedelta(seconds=max(0.0, rng.gauss(60, 120)))
            if trip_type == "package":
                hours = draft.package_hours or 4.0
                duration_s = hours * 3600 * (1 + rng.gauss(0, 0.03))
                actual_distance = rng.uniform(30_000, 110_000)
            else:
                actual_distance = _haversine_m(pickup.lat, pickup.lng, drop.lat, drop.lng) * P.CIRCUITY
                local = actual_pickup.astimezone(IST)
                kmh = P.speed_at_minute(local.hour * 60 + local.minute)
                duration_s = (
                    actual_distance / 1000 / kmh * 3600 * rng.lognormvariate(0, P.DURATION_NOISE_SIGMA)
                )
                if self.is_rain_day(d):
                    duration_s *= P.RAIN_MULTIPLIER
                duration_s += P.HANDLING_S
            actual_drop = actual_pickup + timedelta(seconds=duration_s)
            lifecycle["assigned"] = _ist(d, -150)
            lifecycle["en_route"] = actual_pickup - timedelta(minutes=rng.uniform(15, 40))
            lifecycle["arrived"] = actual_pickup - timedelta(minutes=rng.uniform(0, 8))
            lifecycle["started"] = actual_pickup
            lifecycle["completed"] = actual_drop
        return TripRecord.model_validate(
            {
                "trip_id": _uuid(self.seed, d, key),
                "service_date": d,
                "channel": draft.channel,
                "scheduled_pickup_at": scheduled,
                "pickup_lat": pickup.lat,
                "pickup_lng": pickup.lng,
                "pickup_address": pickup.address,
                "drop_lat": drop.lat,
                "drop_lng": drop.lng,
                "drop_address": drop.address,
                "trip_type": trip_type,
                "package_hours": draft.package_hours,
                "pax": pax,
                "luggage": luggage,
                "vehicle_class": vehicle_class,
                "tags": tags,
                "account_id": account_id,
                "status": status,
                "lifecycle": lifecycle,
                "actual_pickup_at": actual_pickup,
                "actual_drop_at": actual_drop,
                "actual_distance_m": round(actual_distance, 1) if actual_distance is not None else None,
            }
        )


__all__ = ["ZONES", "SyntheticWorld"]
