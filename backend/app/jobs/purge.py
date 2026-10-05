"""purge_here_cache: enforce the HERE 30 day retention rule, and keep ping partitions ahead."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, text, update

from app.core.timeutil import day_start_utc
from app.db.models import EtaLog, LegEstimate
from app.db.session import session_scope
from app.jobs.context import AppContext

PING_DAYS_AHEAD = 3
PING_RETENTION_DAYS = 120


async def purge_here_cache(ctx: AppContext, now: datetime | None = None) -> dict[str, Any]:
    settings = await ctx.settings()
    stamp = now or datetime.now(UTC)
    cutoff = stamp - timedelta(days=settings.here_retention_days)
    async with session_scope(ctx.factory) as session:
        deleted = await session.execute(
            delete(LegEstimate).where(LegEstimate.source == "here", LegEstimate.expires_at <= stamp)
        )
        nulled = await session.execute(
            update(EtaLog)
            .where(
                EtaLog.source == "here",
                EtaLog.computed_at <= cutoff,
                EtaLog.predicted_p50_s.is_not(None),
            )
            .values(predicted_p50_s=None, predicted_p80_s=None, base_s=None)
        )
    # Redis hot keys expire after HOT_CACHE_TTL_S (6 h), well inside the retention window.
    partitions = await ensure_ping_partitions(ctx, (await ctx.today()))
    return {
        "leg_estimates_deleted": deleted.rowcount,  # type: ignore[attr-defined]
        "eta_log_predictions_nulled": nulled.rowcount,  # type: ignore[attr-defined]
        "ping_partitions": partitions,
    }


def _partition_name(d: date) -> str:
    return f"ping_{d.strftime('%Y%m%d')}"


async def ensure_ping_partitions(ctx: AppContext, today: date) -> dict[str, int]:
    """Daily partitions on IST day boundaries for today and the next days; drop old ones."""
    created = 0
    dropped = 0
    async with session_scope(ctx.factory) as session:
        existing = set(
            (
                await session.execute(
                    text(
                        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                        "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'ping'"
                    )
                )
            ).scalars()
        )
        for offset in range(-1, PING_DAYS_AHEAD + 1):
            d = today + timedelta(days=offset)
            name = _partition_name(d)
            if name in existing:
                continue
            start, end = day_start_utc(d), day_start_utc(d + timedelta(days=1))
            # Rows that landed in the default partition for this range must move before attaching.
            await session.execute(
                text(f"CREATE TABLE {name} (LIKE ping INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")
            )
            await session.execute(
                # name is built from a date, never from input.
                text(f"INSERT INTO {name} SELECT * FROM ping_default WHERE ts >= :s AND ts < :e"),  # noqa: S608
                {"s": start, "e": end},
            )
            await session.execute(
                text("DELETE FROM ping_default WHERE ts >= :s AND ts < :e"), {"s": start, "e": end}
            )
            await session.execute(
                text(
                    f"ALTER TABLE ping ATTACH PARTITION {name} "
                    f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                )
            )
            created += 1
        horizon = today - timedelta(days=PING_RETENTION_DAYS)
        for name in sorted(existing):
            if name == "ping_default" or not name[5:].isdigit():
                continue
            if date(int(name[5:9]), int(name[9:11]), int(name[11:13])) < horizon:
                await session.execute(text(f"DROP TABLE {name}"))
                dropped += 1
    return {"created": created, "dropped": dropped}
