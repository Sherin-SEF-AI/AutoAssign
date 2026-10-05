"""LegEstimator: one interface for every caller, providers tried in order.

  1. cache   Redis hot key, then the leg_estimate row (fresh for CACHE_FRESH_S)
  2. HERE    Routing v8 for trip legs and single requests, Matrix v8 for deadhead and hub batches
  3. OSRM    when OSRM_URL is set: table for batches, route for singles (free-flow)
  4. fallback haversine times circuity over the speed profile (always available)

Each step is skipped when unavailable or over budget. A batch always completes.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.adapters.here.client import MAX_MATRIX_SIDE, HereClient
from app.adapters.osrm.client import MAX_TABLE_SIDE, OsrmClient
from app.config import Settings
from app.core.errors import ProviderError, ProviderUnavailable
from app.core.geo import Point, haversine_m
from app.core.logging import get_logger
from app.core.metrics import ETA_FALLBACK, ETA_REQUESTS
from app.core.routing import RawLeg
from app.core.speed import speed_kmh, time_bin_index
from app.core.timeutil import BIN_S, day_start_utc, service_date_of, weekday_type
from app.db.models import LegEstimate as LegEstimateRow
from app.db.session import SessionFactory, session_scope
from app.eta.factors import FactorTable
from app.eta.types import CacheKey, LegEstimate, LegKind, LegRequest, cache_key

log = get_logger("eta")
DB_CHUNK = 400


@dataclass(slots=True)
class EstimateStats:
    by_source: Counter[str] = field(default_factory=Counter)
    by_kind_source: Counter[str] = field(default_factory=Counter)
    cache_hits: int = 0
    here_routing_calls: int = 0
    here_matrix_calls: int = 0
    here_matrix_elements: int = 0
    osrm_calls: int = 0
    provider_errors: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "by_source": dict(sorted(self.by_source.items())),
            "by_kind_source": dict(sorted(self.by_kind_source.items())),
            "cache_hits": self.cache_hits,
            "here_routing_calls": self.here_routing_calls,
            "here_matrix_calls": self.here_matrix_calls,
            "here_matrix_elements": self.here_matrix_elements,
            "osrm_calls": self.osrm_calls,
            "provider_errors": self.provider_errors,
            "skipped": dict(sorted(self.skipped.items())),
        }


def fallback_leg(settings: Settings, origin: Point, dest: Point, depart_at: datetime) -> RawLeg:
    distance = haversine_m(origin, dest) * settings.circuity
    kmh = speed_kmh(settings.speed_profile_json, depart_at)
    return RawLeg(duration_s=distance / 1000.0 / kmh * 3600.0, distance_m=distance, source="fallback")


def bin_start(depart_at: datetime) -> datetime:
    day = service_date_of(depart_at)
    start = day_start_utc(day)
    offset = int((depart_at - start).total_seconds()) // BIN_S * BIN_S
    return start + timedelta(seconds=offset)


class LegEstimator:
    def __init__(
        self,
        *,
        settings: Settings,
        factory: SessionFactory,
        redis: Redis,
        factors: FactorTable,
        here: HereClient | None = None,
        osrm: OsrmClient | None = None,
        concurrency: int = 8,
        persist: bool = True,
    ):
        self.settings = settings
        self.factory = factory
        self.redis = redis
        self.factors = factors
        self.here = here
        self.osrm = osrm
        self.persist = persist
        self.stats = EstimateStats()
        self._sem = asyncio.Semaphore(concurrency)
        self._here_down: set[str] = set()

    # Public API -------------------------------------------------------------------

    async def estimate(
        self, origin: Point, dest: Point, depart_at: datetime, kind: LegKind, *, live: bool = False
    ) -> LegEstimate:
        """One leg. live=True skips cache reads and asks HERE with this departure time."""
        req = LegRequest(origin, dest, depart_at)
        if not live:
            return (await self.estimate_many([req], kind))[0]
        key = cache_key(kind, req)
        found = await self._from_providers({key: req}, kind, prefer_routing=True)
        await self._persist(kind, found)
        est = found[key][0]
        self._count(kind, est.source)
        return est

    async def estimate_many(self, requests: Sequence[LegRequest], kind: LegKind) -> list[LegEstimate]:
        keys = [cache_key(kind, r) for r in requests]
        unique: dict[CacheKey, LegRequest] = {}
        for k, r in zip(keys, requests, strict=True):
            unique.setdefault(k, r)
        results: dict[CacheKey, LegEstimate] = {}
        results.update(await self._from_hot(unique))
        missing = {k: r for k, r in unique.items() if k not in results}
        if missing:
            db_hits = await self._from_db(missing)
            results.update(db_hits)
            if db_hits:
                await self._write_hot({k: (v, None) for k, v in db_hits.items()})
        self.stats.cache_hits += len(results)
        for _ in results:
            ETA_REQUESTS.labels(provider="cache", outcome="hit").inc()
        missing = {k: r for k, r in unique.items() if k not in results}
        if missing:
            found = await self._from_providers(missing, kind, prefer_routing=kind == "trip")
            await self._persist(kind, found)
            results.update({k: v[0] for k, v in found.items()})
        out = [results[k] for k in keys]
        for est in out:
            self._count(kind, est.source)
        return out

    # Quantiles --------------------------------------------------------------------

    def apply_factors(
        self, raw: RawLeg, kind: str, origin: Point, dest: Point, depart_at: datetime
    ) -> LegEstimate:
        basis = raw.source if raw.source in ("here", "osrm") else "fallback"
        base = raw.free_flow_s if raw.source == "osrm" and raw.free_flow_s is not None else raw.duration_s
        if kind == "trip":
            base += self.settings.handling_s
        tb = time_bin_index(self.settings.speed_profile_json, depart_at)
        wd = weekday_type(service_date_of(depart_at))
        f = self.factors.lookup(basis, tb, wd, self.factors.cluster_of(origin))
        r50 = f.r50
        r80 = max(f.r80, r50)
        r90 = max(f.r90, r80)
        distance = (
            raw.distance_m if raw.distance_m > 0 else haversine_m(origin, dest) * self.settings.circuity
        )
        return LegEstimate(
            p50_s=base * r50,
            p80_s=base * r80,
            p90_s=base * r90,
            distance_m=distance,
            source=raw.source,
            provider_ref=raw.provider_ref,
            base_s=base,
        )

    # Cache ------------------------------------------------------------------------

    def _fresh(self, created_at: datetime) -> bool:
        return datetime.now(UTC) - created_at < timedelta(seconds=self.settings.cache_fresh_s)

    async def _from_hot(self, unique: dict[CacheKey, LegRequest]) -> dict[CacheKey, LegEstimate]:
        if not unique:
            return {}
        keys = list(unique)
        raws = await self.redis.mget([k.redis_key() for k in keys])
        out: dict[CacheKey, LegEstimate] = {}
        for k, raw in zip(keys, raws, strict=True):
            if raw is None:
                continue
            try:
                data = json.loads(raw)
                created = datetime.fromisoformat(data["created_at"])
            except (ValueError, KeyError, TypeError):
                continue
            if data.get("source") == "fallback" or not self._fresh(created):
                continue
            out[k] = LegEstimate(
                p50_s=float(data["p50_s"]),
                p80_s=float(data["p80_s"]),
                p90_s=float(data["p90_s"]),
                distance_m=float(data["distance_m"]),
                source=str(data["source"]),
                provider_ref=data.get("provider_ref"),
                base_s=float(data.get("base_s", 0.0)),
                cached=True,
            )
        return out

    async def _from_db(self, missing: dict[CacheKey, LegRequest]) -> dict[CacheKey, LegEstimate]:
        out: dict[CacheKey, LegEstimate] = {}
        keys = list(missing)
        async with self.factory() as session:
            for i in range(0, len(keys), DB_CHUNK):
                chunk = keys[i : i + DB_CHUNK]
                cols = tuple_(
                    LegEstimateRow.kind,
                    LegEstimateRow.origin_h3,
                    LegEstimateRow.dest_h3,
                    LegEstimateRow.depart_bin,
                    LegEstimateRow.service_date,
                )
                stmt = select(LegEstimateRow).where(
                    cols.in_([(k.kind, k.origin_h3, k.dest_h3, k.depart_bin, k.service_date) for k in chunk]),
                    LegEstimateRow.source != "fallback",
                )
                for row in (await session.execute(stmt)).scalars():
                    if not self._fresh(row.created_at):
                        continue
                    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
                        continue
                    key = CacheKey(row.kind, row.origin_h3, row.dest_h3, row.depart_bin, row.service_date)
                    base = float((row.provider_payload or {}).get("base_s", 0.0))
                    out[key] = LegEstimate(
                        p50_s=row.p50_s,
                        p80_s=row.p80_s,
                        p90_s=row.p90_s,
                        distance_m=row.distance_m,
                        source=row.source,
                        base_s=base,
                        cached=True,
                    )
        return out

    async def _write_hot(self, items: dict[CacheKey, tuple[LegEstimate, datetime | None]]) -> None:
        if not items:
            return
        pipe = self.redis.pipeline()
        now = datetime.now(UTC).isoformat()
        for key, (est, created) in items.items():
            if est.source == "fallback":
                continue
            payload = {
                "p50_s": est.p50_s,
                "p80_s": est.p80_s,
                "p90_s": est.p90_s,
                "distance_m": est.distance_m,
                "source": est.source,
                "provider_ref": est.provider_ref,
                "base_s": est.base_s,
                "created_at": created.isoformat() if created else now,
            }
            pipe.set(key.redis_key(), json.dumps(payload), ex=self.settings.hot_cache_ttl_s)
        await pipe.execute()

    async def _persist(self, kind: str, found: dict[CacheKey, tuple[LegEstimate, dict[str, Any]]]) -> None:
        if not found or not self.persist:
            return
        now = datetime.now(UTC)
        retention = timedelta(days=self.settings.here_retention_days)
        rows = []
        for key, (est, ctx) in found.items():
            payload = dict(ctx.get("payload") or {})
            payload["base_s"] = est.base_s
            rows.append(
                {
                    "kind": kind,
                    "origin_h3": key.origin_h3,
                    "dest_h3": key.dest_h3,
                    "origin_lat": ctx["origin"].lat,
                    "origin_lng": ctx["origin"].lng,
                    "dest_lat": ctx["dest"].lat,
                    "dest_lng": ctx["dest"].lng,
                    "depart_bin": key.depart_bin,
                    "service_date": key.service_date,
                    "source": est.source,
                    "p50_s": est.p50_s,
                    "p80_s": est.p80_s,
                    "p90_s": est.p90_s,
                    "distance_m": est.distance_m,
                    "provider_payload": payload if est.source == "here" else {"base_s": est.base_s},
                    "created_at": now,
                    "expires_at": now + retention if est.source == "here" else None,
                }
            )
        async with session_scope(self.factory) as session:
            for i in range(0, len(rows), DB_CHUNK):
                stmt = insert(LegEstimateRow).values(rows[i : i + DB_CHUNK])
                excluded = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_leg_estimate_key",
                    set_={
                        c: excluded[c]
                        for c in (
                            "origin_lat",
                            "origin_lng",
                            "dest_lat",
                            "dest_lng",
                            "source",
                            "p50_s",
                            "p80_s",
                            "p90_s",
                            "distance_m",
                            "provider_payload",
                            "created_at",
                            "expires_at",
                        )
                    },
                    where=or_(LegEstimateRow.source == "fallback", excluded.source != "fallback"),
                )
                await session.execute(stmt)
        await self._write_hot({k: (v[0], now) for k, v in found.items()})

    # Providers --------------------------------------------------------------------

    async def _from_providers(
        self, missing: dict[CacheKey, LegRequest], kind: str, *, prefer_routing: bool
    ) -> dict[CacheKey, tuple[LegEstimate, dict[str, Any]]]:
        out: dict[CacheKey, tuple[LegEstimate, dict[str, Any]]] = {}
        pending = dict(missing)
        if self.here is not None and self.settings.here_available:
            if prefer_routing or len(pending) == 1:
                out.update(await self._here_routing(pending, kind))
            else:
                out.update(await self._here_matrix(pending, kind))
            pending = {k: r for k, r in pending.items() if k not in out}
        elif pending:
            self.stats.skipped["here_disabled"] += len(pending)
        if pending and self.osrm is not None and self.settings.osrm_available:
            out.update(await self._osrm(pending, kind))
            pending = {k: r for k, r in pending.items() if k not in out}
        if pending:
            log.warning("eta_fallback", kind=kind, legs=len(pending))
            for key, req in pending.items():
                raw = fallback_leg(self.settings, req.origin, req.dest, req.depart_at)
                est = self.apply_factors(raw, kind, req.origin, req.dest, req.depart_at)
                out[key] = (est, {"origin": req.origin, "dest": req.dest})
                ETA_FALLBACK.inc()
                ETA_REQUESTS.labels(provider="fallback", outcome="ok").inc()
        return out

    async def _here_routing(
        self, pending: dict[CacheKey, LegRequest], kind: str
    ) -> dict[CacheKey, tuple[LegEstimate, dict[str, Any]]]:
        assert self.here is not None
        out: dict[CacheKey, tuple[LegEstimate, dict[str, Any]]] = {}
        stop = asyncio.Event()

        async def one(key: CacheKey, req: LegRequest) -> None:
            if stop.is_set():
                return
            async with self._sem:
                if stop.is_set():
                    return
                try:
                    assert self.here is not None
                    self.stats.here_routing_calls += 1
                    raw = await self.here.route(req.origin, req.dest, req.depart_at, datetime.now(UTC))
                except ProviderUnavailable as exc:
                    stop.set()
                    self.stats.here_routing_calls -= 1
                    self.stats.skipped[f"here_routing:{exc}"] += 1
                    ETA_REQUESTS.labels(provider="here", outcome="skipped").inc()
                    return
                except ProviderError as exc:
                    self.stats.provider_errors += 1
                    ETA_REQUESTS.labels(provider="here", outcome="error").inc()
                    log.warning("here_routing_failed", error=str(exc))
                    return
            est = self.apply_factors(raw, kind, req.origin, req.dest, req.depart_at)
            out[key] = (est, {"origin": req.origin, "dest": req.dest, "payload": raw.payload})
            ETA_REQUESTS.labels(provider="here", outcome="ok").inc()

        ordered = sorted(pending.items(), key=lambda kv: (kv[1].depart_at, kv[0].origin_h3, kv[0].dest_h3))
        await asyncio.gather(*(one(k, r) for k, r in ordered))
        return out

    async def _here_matrix(
        self, pending: dict[CacheKey, LegRequest], kind: str
    ) -> dict[CacheKey, tuple[LegEstimate, dict[str, Any]]]:
        assert self.here is not None
        out: dict[CacheKey, tuple[LegEstimate, dict[str, Any]]] = {}
        for depart, blocks in _matrix_blocks(pending, MAX_MATRIX_SIDE):
            for origins, dests, wanted in blocks:
                try:
                    self.stats.here_matrix_calls += 1
                    self.stats.here_matrix_elements += len(origins) * len(dests)
                    grid = await self.here.matrix(
                        [p for _, p in origins], [p for _, p in dests], depart, datetime.now(UTC)
                    )
                except ProviderUnavailable as exc:
                    self.stats.here_matrix_calls -= 1
                    self.stats.here_matrix_elements -= len(origins) * len(dests)
                    self.stats.skipped[f"here_matrix:{exc}"] += 1
                    ETA_REQUESTS.labels(provider="here", outcome="skipped").inc()
                    return out
                except ProviderError as exc:
                    self.stats.provider_errors += 1
                    ETA_REQUESTS.labels(provider="here", outcome="error").inc()
                    log.warning("here_matrix_failed", error=str(exc))
                    continue
                self._collect_grid(grid, origins, dests, wanted, depart, kind, out)
        return out

    async def _osrm(
        self, pending: dict[CacheKey, LegRequest], kind: str
    ) -> dict[CacheKey, tuple[LegEstimate, dict[str, Any]]]:
        assert self.osrm is not None
        out: dict[CacheKey, tuple[LegEstimate, dict[str, Any]]] = {}
        if len(pending) == 1:
            key, req = next(iter(pending.items()))
            try:
                self.stats.osrm_calls += 1
                raw = await self.osrm.route(req.origin, req.dest)
            except ProviderError as exc:
                self.stats.provider_errors += 1
                ETA_REQUESTS.labels(provider="osrm", outcome="error").inc()
                log.warning("osrm_route_failed", error=str(exc))
                return out
            out[key] = (
                self.apply_factors(raw, kind, req.origin, req.dest, req.depart_at),
                {"origin": req.origin, "dest": req.dest},
            )
            ETA_REQUESTS.labels(provider="osrm", outcome="ok").inc()
            return out
        for depart, blocks in _matrix_blocks(pending, MAX_TABLE_SIDE):
            for origins, dests, wanted in blocks:
                try:
                    self.stats.osrm_calls += 1
                    grid = await self.osrm.table([p for _, p in origins], [p for _, p in dests])
                except ProviderError as exc:
                    self.stats.provider_errors += 1
                    ETA_REQUESTS.labels(provider="osrm", outcome="error").inc()
                    log.warning("osrm_table_failed", error=str(exc))
                    if isinstance(exc, ProviderUnavailable):
                        return out
                    continue
                self._collect_grid(grid, origins, dests, wanted, depart, kind, out)
        return out

    def _collect_grid(
        self,
        grid: list[list[RawLeg | None]],
        origins: list[tuple[str, Point]],
        dests: list[tuple[str, Point]],
        wanted: dict[tuple[str, str], list[tuple[CacheKey, LegRequest]]],
        depart: datetime,
        kind: str,
        out: dict[CacheKey, tuple[LegEstimate, dict[str, Any]]],
    ) -> None:
        for i, (o_h3, _o_pt) in enumerate(origins):
            for j, (d_h3, _d_pt) in enumerate(dests):
                raw = grid[i][j] if i < len(grid) and j < len(grid[i]) else None
                if raw is None:
                    continue
                for key, req in wanted.get((o_h3, d_h3), []):
                    est = self.apply_factors(raw, kind, req.origin, req.dest, req.depart_at)
                    out[key] = (est, {"origin": req.origin, "dest": req.dest, "payload": raw.payload})
                    ETA_REQUESTS.labels(provider=raw.source, outcome="ok").inc()
        _ = depart

    def _count(self, kind: str, source: str) -> None:
        self.stats.by_source[source] += 1
        self.stats.by_kind_source[f"{kind}:{source}"] += 1


Block = tuple[
    list[tuple[str, Point]],
    list[tuple[str, Point]],
    dict[tuple[str, str], list[tuple[CacheKey, LegRequest]]],
]


def _matrix_blocks(pending: dict[CacheKey, LegRequest], side: int) -> Iterable[tuple[datetime, list[Block]]]:
    """Group requests by 15 minute departure bin, then cut into at most side x side blocks that
    contain at least one wanted pair. Deterministic ordering by H3 ids."""
    by_bin: dict[tuple[str, int], list[tuple[CacheKey, LegRequest]]] = defaultdict(list)
    for key, req in pending.items():
        by_bin[(key.service_date.isoformat(), key.depart_bin)].append((key, req))
    for bin_key in sorted(by_bin):
        items = by_bin[bin_key]
        depart = bin_start(min(r.depart_at for _, r in items))
        wanted: dict[tuple[str, str], list[tuple[CacheKey, LegRequest]]] = defaultdict(list)
        o_pts: dict[str, Point] = {}
        d_pts: dict[str, Point] = {}
        for key, req in items:
            wanted[(key.origin_h3, key.dest_h3)].append((key, req))
            o_pts.setdefault(key.origin_h3, req.origin)
            d_pts.setdefault(key.dest_h3, req.dest)
        origin_ids = sorted(o_pts)
        blocks: list[Block] = []
        for oi in range(0, len(origin_ids), side):
            o_chunk = origin_ids[oi : oi + side]
            needed_d = sorted({d for (o, d) in wanted if o in set(o_chunk)})
            for di in range(0, len(needed_d), side):
                d_chunk = needed_d[di : di + side]
                d_set = set(d_chunk)
                o_used = [o for o in o_chunk if any((o, d) in wanted for d in d_set)]
                if not o_used:
                    continue
                sub = {(o, d): wanted[(o, d)] for o in o_used for d in d_chunk if (o, d) in wanted}
                blocks.append(([(o, o_pts[o]) for o in o_used], [(d, d_pts[d]) for d in d_chunk], sub))
        yield depart, blocks
