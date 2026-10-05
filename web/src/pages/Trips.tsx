import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useServiceDate, useSearchParam } from "../state/date";
import { TRIP_STATUSES, useTrip, useTrips, type TripDetail, type TripRow } from "../state/queries";
import { useDebounced } from "../lib/useDebounced";
import { fmtDateTime, fmtTime, secToMin } from "../lib/time";
import { Drawer } from "../components/Drawer";
import { ErrorBanner } from "../components/Toast";
import { TripTags } from "../components/TripTags";
import { Badge, EmptyRow, JsonBlock, Section, Spinner, StatusChip, TableWrap, Td, Th, btnCls, fmtNum, inputCls } from "../components/ui";

const COLS = 13;

export default function TripsPage() {
  const [date] = useServiceDate();
  const [qParam, setQParam] = useSearchParam("q");
  const [status, setStatus] = useSearchParam("status");
  const [tripId, setTripId] = useSearchParam("trip");
  const [qInput, setQInput] = useState(qParam ?? "");
  const q = useDebounced(qInput.trim(), 300);

  // Mirror the debounced search into the URL so the view is shareable.
  useEffect(() => {
    if ((qParam ?? "") !== q) setQParam(q || null);
  }, [q]);

  const trips = useTrips(date, q, status ?? "");
  const rows = useMemo(() => trips.data?.pages.flatMap((p) => p.items) ?? [], [trips.data]);
  const total = trips.data?.pages[0]?.total;

  return (
    <div className="space-y-2">
      <Section
        title={
          <>
            Trips <span className="font-normal text-neutral-500">{date}</span>
          </>
        }
        right={
          <>
            <input
              type="search"
              placeholder="Search id, address, account..."
              className={`${inputCls} w-60`}
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
            />
            <select className={inputCls} value={status ?? ""} onChange={(e) => setStatus(e.target.value || null)} aria-label="Status filter">
              <option value="">All statuses</option>
              {TRIP_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <span className="text-xs text-neutral-500">
              {rows.length}
              {typeof total === "number" ? ` of ${total}` : ""} shown
            </span>
            {trips.isFetching && <Spinner label="Refreshing" />}
          </>
        }
      >
        {trips.error && <ErrorBanner error={trips.error} onRetry={() => void trips.refetch()} />}
        <TableWrap className="max-h-[calc(100vh-170px)]">
          <thead>
            <tr>
              <Th>Pickup (IST)</Th>
              <Th>Status</Th>
              <Th>Channel</Th>
              <Th>Type</Th>
              <Th>Class</Th>
              <Th>Tags</Th>
              <Th>Pickup address</Th>
              <Th>Drop address</Th>
              <Th>Driver</Th>
              <Th>Planned pickup</Th>
              <Th className="text-right">p50 min</Th>
              <Th className="text-right">p80 min</Th>
              <Th>Est. source</Th>
            </tr>
          </thead>
          <tbody>
            {trips.isLoading && <EmptyRow cols={COLS}><Spinner /></EmptyRow>}
            {!trips.isLoading && rows.length === 0 && <EmptyRow cols={COLS}>No trips for this date and filter</EmptyRow>}
            {rows.map((t) => (
              <TripTr key={t.trip_id} t={t} selected={t.trip_id === tripId} onClick={() => setTripId(t.trip_id)} />
            ))}
          </tbody>
        </TableWrap>
        {trips.hasNextPage && (
          <div className="flex justify-center">
            <button type="button" className={btnCls} disabled={trips.isFetchingNextPage} onClick={() => void trips.fetchNextPage()}>
              {trips.isFetchingNextPage ? "Loading..." : "Load more"}
            </button>
          </div>
        )}
      </Section>
      <Drawer open={!!tripId} title={tripId ? `Trip ${tripId}` : ""} onClose={() => setTripId(null)}>
        {tripId && <TripDetailView tripId={tripId} />}
      </Drawer>
    </div>
  );
}

function TripTr({ t, selected, onClick }: { t: TripRow; selected: boolean; onClick: () => void }) {
  return (
    <tr onClick={onClick} className={`cursor-pointer hover:bg-sky-50 ${selected ? "bg-sky-100" : ""}`}>
      <Td className="whitespace-nowrap font-mono">{fmtTime(t.scheduled_pickup_at)}</Td>
      <Td>
        <StatusChip status={t.status} />
      </Td>
      <Td>{t.channel}</Td>
      <Td className="whitespace-nowrap">
        {t.trip_type}
        {t.package_hours ? ` ${t.package_hours}h` : ""}
      </Td>
      <Td>{t.vehicle_class}</Td>
      <Td>
        <TripTags tags={t.tags} accountId={t.account_id} />
      </Td>
      <Td className="max-w-[220px] truncate" title={t.pickup_address}>
        {t.pickup_address}
      </Td>
      <Td className="max-w-[220px] truncate" title={t.drop_address}>
        {t.drop_address}
      </Td>
      <Td className="whitespace-nowrap">
        {t.driver_name ?? (t.unassigned_reason ? <span className="text-amber-700" title={t.unassigned_reason}>unassigned</span> : <span className="text-neutral-400">-</span>)}
        {t.locked && <span className="ml-1"><Badge>locked</Badge></span>}
      </Td>
      <Td className="whitespace-nowrap font-mono">{fmtTime(t.planned_pickup_at)}</Td>
      <Td className="text-right font-mono">{secToMin(t.estimate_p50_s)}</Td>
      <Td className="text-right font-mono">{secToMin(t.estimate_p80_s)}</Td>
      <Td>{t.estimate_source ?? "-"}</Td>
    </tr>
  );
}

function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-neutral-500">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

function TripDetailView({ tripId }: { tripId: string }) {
  const q = useTrip(tripId);
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data) return null;
  return <TripDetailBody d={q.data} />;
}

function TripDetailBody({ d }: { d: TripDetail }) {
  const t = d.trip;
  const lifecycle = Object.entries(d.lifecycle as Record<string, unknown>)
    .map(([k, v]) => [k, typeof v === "string" ? v : null] as const)
    .sort((a, b) => (a[1] ?? "").localeCompare(b[1] ?? ""));
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1">
        <KV k="Trip id"><span className="font-mono">{t.trip_id}</span></KV>
        <KV k="Status"><StatusChip status={t.status} /></KV>
        <KV k="Service date">{t.service_date}</KV>
        <KV k="Scheduled pickup">{fmtDateTime(t.scheduled_pickup_at)}</KV>
        <KV k="Channel">{t.channel}</KV>
        <KV k="Type">{t.trip_type}{t.package_hours ? ` (${t.package_hours} h package)` : ""}</KV>
        <KV k="Vehicle class">{t.vehicle_class}</KV>
        <KV k="Pax / luggage">{t.pax} / {t.luggage}</KV>
        <KV k="Tags"><TripTags tags={t.tags} accountId={t.account_id} /></KV>
        <KV k="Pickup">{t.pickup_address} <span className="font-mono text-neutral-500">({t.pickup_lat.toFixed(5)}, {t.pickup_lng.toFixed(5)})</span></KV>
        <KV k="Drop">{t.drop_address} <span className="font-mono text-neutral-500">({t.drop_lat.toFixed(5)}, {t.drop_lng.toFixed(5)})</span></KV>
        <KV k="Driver">{t.driver_name ?? "-"}{t.driver_id ? <span className="ml-1 font-mono text-neutral-500">{t.driver_id}</span> : null}</KV>
        <KV k="Locked">{t.locked ? "yes" : "no"}</KV>
        <KV k="Unassigned reason">{t.unassigned_reason ?? "-"}</KV>
        <KV k="Planned pickup">{fmtDateTime(t.planned_pickup_at)}</KV>
        <KV k="Planned drop">{fmtDateTime(t.planned_drop_at)}</KV>
        <KV k="Estimate p50 / p80">{secToMin(t.estimate_p50_s)} / {secToMin(t.estimate_p80_s)} min ({t.estimate_source ?? "no source"})</KV>
        <KV k="Actual pickup">{fmtDateTime(t.actual_pickup_at)}</KV>
        <KV k="Actual drop">{fmtDateTime(t.actual_drop_at)}</KV>
        <KV k="Actual distance">{t.actual_distance_m != null ? `${fmtNum(t.actual_distance_m / 1000, 1)} km` : "-"}</KV>
        <KV k="Snapshot"><span className="font-mono">{d.snapshot_id}</span></KV>
      </dl>

      <div>
        <h3 className="mb-1 font-semibold">Lifecycle (IST)</h3>
        {lifecycle.length === 0 ? (
          <div className="text-neutral-500">No lifecycle events</div>
        ) : (
          <TableWrap>
            <tbody>
              {lifecycle.map(([k, v]) => (
                <tr key={k}>
                  <Td className="w-40"><StatusChip status={k} /></Td>
                  <Td className="font-mono">{v ? fmtDateTime(v) : "-"}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </div>

      <div>
        <h3 className="mb-1 font-semibold">Estimate history</h3>
        <TableWrap>
          <thead>
            <tr>
              <Th>Kind</Th>
              <Th>Source</Th>
              <Th className="text-right">Bin</Th>
              <Th className="text-right">p50</Th>
              <Th className="text-right">p80</Th>
              <Th className="text-right">p90</Th>
              <Th className="text-right">km</Th>
              <Th>Created</Th>
              <Th>Expires</Th>
            </tr>
          </thead>
          <tbody>
            {d.estimates.length === 0 && <EmptyRow cols={9}>No estimates</EmptyRow>}
            {d.estimates.map((e, i) => (
              <tr key={i}>
                <Td>{e.kind}</Td>
                <Td>{e.source}</Td>
                <Td className="text-right font-mono">{e.depart_bin}</Td>
                <Td className="text-right font-mono">{secToMin(e.p50_s)}</Td>
                <Td className="text-right font-mono">{secToMin(e.p80_s)}</Td>
                <Td className="text-right font-mono">{secToMin(e.p90_s)}</Td>
                <Td className="text-right font-mono">{fmtNum(e.distance_m / 1000, 1)}</Td>
                <Td className="whitespace-nowrap font-mono">{fmtDateTime(e.created_at)}</Td>
                <Td className="whitespace-nowrap font-mono">{fmtDateTime(e.expires_at)}</Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
        <div className="mt-0.5 text-[11px] text-neutral-500">Durations in minutes.</div>
      </div>

      <div>
        <h3 className="mb-1 font-semibold">ETA log (actuals)</h3>
        <TableWrap>
          <thead>
            <tr>
              <Th>Leg</Th>
              <Th>Source</Th>
              <Th className="text-right">Pred p50</Th>
              <Th className="text-right">Pred p80</Th>
              <Th className="text-right">Actual</Th>
              <Th className="text-right">Residual</Th>
              <Th>In p80</Th>
              <Th>Computed</Th>
            </tr>
          </thead>
          <tbody>
            {d.eta_log.length === 0 && <EmptyRow cols={8}>No actuals recorded</EmptyRow>}
            {d.eta_log.map((e, i) => (
              <tr key={i}>
                <Td>{e.leg_kind}</Td>
                <Td>{e.source}</Td>
                <Td className="text-right font-mono">{secToMin(e.predicted_p50_s)}</Td>
                <Td className="text-right font-mono">{secToMin(e.predicted_p80_s)}</Td>
                <Td className="text-right font-mono">{secToMin(e.actual_s)}</Td>
                <Td className={`text-right font-mono ${Math.abs(e.residual_ratio) > 0.25 ? "text-red-700" : ""}`}>{e.residual_ratio.toFixed(3)}</Td>
                <Td>{e.covered_p80 == null ? "-" : e.covered_p80 ? <Badge tone="green">yes</Badge> : <Badge tone="red">no</Badge>}</Td>
                <Td className="whitespace-nowrap font-mono">{fmtDateTime(e.computed_at)}</Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      </div>

      <div>
        <h3 className="mb-1 font-semibold">Raw</h3>
        <JsonBlock value={d} label="raw response" />
      </div>
    </div>
  );
}
