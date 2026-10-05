import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../state/auth";
import { useSearchParam, useServiceDate } from "../state/date";
import { useSimNow } from "../state/sim";
import {
  usePlan,
  usePlans,
  useRepairs,
  useResolve,
  useRunJob,
  useLive,
  useSimControl,
  useSimStatus,
  type Assignment,
  type DriverLane,
  type LiveDriver,
  type Repair,
} from "../state/queries";
import { fmtDateTime, fmtTime, fmtTimeSec } from "../lib/time";
import { driverColor } from "../lib/planStyle";
import { Timeline } from "../components/Timeline";
import { TripPanel } from "../components/TripPanel";
import { ErrorBanner, toast } from "../components/Toast";
import { Section, Spinner, StatusChip, btnCls, btnPrimaryCls, fmtNum, inputCls } from "../components/ui";

const MapView = lazy(() => import("../components/MapView"));

const EMPTY_SET = new Set<string>();
const EMPTY_DIFF = new Map<string, { gained: number; lost: number }>();

/** Risk from the monitor; recomputed from slack when the label is missing (green > 15 min, amber 5 to 15, red < 5 or late). */
function riskOf(d: LiveDriver | undefined): "green" | "amber" | "red" | null {
  if (!d) return null;
  if (d.risk === "green" || d.risk === "amber" || d.risk === "red") return d.risk;
  if (d.slack_s == null) return null;
  const m = d.slack_s / 60;
  return m > 15 ? "green" : m >= 5 ? "amber" : "red";
}

const RISK_CLS: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-800 border-emerald-300",
  amber: "bg-amber-100 text-amber-800 border-amber-300",
  red: "bg-red-100 text-red-800 border-red-300",
};

function RiskBadge({ d }: { d: LiveDriver | undefined }) {
  const r = riskOf(d);
  if (!r || !d) return <span className="text-[10px] text-neutral-400">no data</span>;
  const slack = d.slack_s == null ? "-" : `${Math.round(d.slack_s / 60)}m`;
  return (
    <span className={`whitespace-nowrap rounded border px-1 text-[10px] font-semibold ${RISK_CLS[r]}`} title={`risk ${r}, slack ${slack}`}>
      {r} {slack}
    </span>
  );
}

export default function LivePage() {
  const { role, isAdmin } = useAuth();
  const canEdit = role === "admin" || role === "ops";
  const [date] = useServiceDate();
  const plans = usePlans(date);
  const [planParam, setPlanParam] = useSearchParam("plan");
  const list = plans.data ?? [];
  const published = list.find((p) => p.status === "published") ?? null;
  // Follow the published version (new versions arrive via plan.* SSE) unless one is chosen explicitly.
  const explicit = planParam ? (list.find((p) => p.plan_id === planParam) ?? null) : null;
  const operative = explicit ?? published ?? list[0] ?? null;
  const following = !explicit;
  const plan = usePlan(operative?.plan_id ?? null);
  const live = useLive(date);
  const repairs = useRepairs(date);
  const sim = useSimNow();
  const resolve = useResolve();
  const runJob = useRunJob();

  const [selectedDriverId, setSelectedDriverId] = useState<string | null>(null);
  const [panelTrip, setPanelTrip] = useState<string | null>(null);

  // Wall clock tick for the now line when no sim runs for this date.
  const [wall, setWall] = useState(Date.now());
  useEffect(() => {
    const h = window.setInterval(() => setWall(Date.now()), 15_000);
    return () => window.clearInterval(h);
  }, []);
  const simForDate = sim.exists && sim.serviceDate === date && sim.simMs !== null;
  const nowMs = simForDate ? sim.simMs : wall;

  const lanes = useMemo<DriverLane[]>(() => {
    const ls = [...(plan.data?.drivers ?? [])];
    ls.sort((a, b) => Number(b.available) - Number(a.available) || (a.shift_start_at ?? "").localeCompare(b.shift_start_at ?? "") || a.name.localeCompare(b.name));
    return ls;
  }, [plan.data]);
  const unassigned = plan.data?.unassigned ?? [];
  const liveById = useMemo(() => new Map((live.data?.drivers ?? []).map((d) => [d.driver_id, d])), [live.data]);
  const colorIndex = useMemo(() => new Map([...lanes].sort((a, b) => a.driver_id.localeCompare(b.driver_id)).map((l, i) => [l.driver_id, i])), [lanes]);
  const colorFor = useCallback((id: string) => driverColor(id, colorIndex.get(id)), [colorIndex]);
  const livePositions = useMemo(
    () =>
      (live.data?.drivers ?? [])
        .filter((d) => d.lat != null && d.lng != null)
        .map((d) => ({ driver_id: d.driver_id, lat: d.lat as number, lng: d.lng as number, risk: riskOf(d) ?? "unknown" })),
    [live.data],
  );
  const riskCounts = useMemo(() => {
    const c = { green: 0, amber: 0, red: 0 };
    for (const d of live.data?.drivers ?? []) {
      const r = riskOf(d);
      if (r) c[r]++;
    }
    return c;
  }, [live.data]);

  const doResolve = (lane: DriverLane) => {
    if (!operative) return;
    resolve.mutate(
      { planId: operative.plan_id, driverIds: [lane.driver_id], reason: `manual re-solve of ${lane.name} from live`, includeFloat: true },
      {
        onSuccess: (out) => {
          if (out.outcome === "no_feasible_repair") toast.warning("No feasible repair", `${lane.name}: ${out.unassigned.length} trips left unassigned`);
          else toast.success(`Re-solve ${out.outcome}`, `${out.freed.length} trips freed, ${out.unassigned.length} unassigned${out.plan ? `, new v${out.plan.version}` : ""}`);
          if (out.plan) setPlanParam(out.plan.status === "published" ? null : out.plan.plan_id);
        },
      },
    );
  };

  const laneExtra = (lane: DriverLane) => {
    const d = liveById.get(lane.driver_id);
    return (
      <>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <RiskBadge d={d} />
          <span className="truncate text-[10px] text-neutral-500" title={d?.source ? `source ${d.source}` : ""}>
            {d?.predicted_arrival_at ? `eta ${fmtTime(d.predicted_arrival_at)}` : ""}
            {d?.source ? ` ${d.source}` : ""}
          </span>
        </div>
        {canEdit && lane.available && (
          <button
            type="button"
            className="shrink-0 rounded border border-neutral-300 bg-white px-1 py-px text-[10px] hover:bg-neutral-100 disabled:opacity-50"
            disabled={resolve.isPending || !operative}
            onClick={() => doResolve(lane)}
            title="Re-solve this driver's remaining trips"
          >
            Re-solve
          </button>
        )}
      </>
    );
  };

  const panel = useMemo(() => {
    if (!panelTrip) return null;
    for (const l of lanes) {
      const a = l.assignments.find((x) => x.trip_id === panelTrip);
      if (a) return { a, lane: l as DriverLane | null };
    }
    const u = unassigned.find((x) => x.trip_id === panelTrip);
    return u ? { a: u, lane: null } : null;
  }, [panelTrip, lanes, unassigned]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <h1 className="text-sm font-semibold">Live {date}</h1>
        {operative ? (
          <>
            <span className="font-mono">v{operative.version}</span>
            <StatusChip status={operative.status} />
            {following ? (
              <span className="text-neutral-500">following {published ? "published" : "newest"} version</span>
            ) : (
              <button type="button" className={btnCls} onClick={() => setPlanParam(null)}>
                Follow published
              </button>
            )}
          </>
        ) : (
          <span className="text-neutral-500">{plans.isLoading ? "Loading plans..." : "No plan for this date"}</span>
        )}
        <span className="text-neutral-500">
          now {fmtTimeSec(nowMs)} IST {simForDate ? "(simulated)" : "(wall clock)"}
        </span>
        <span className="text-neutral-500">monitor as of {live.data?.as_of ? fmtDateTime(live.data.as_of) : "never"}</span>
        <span className="flex gap-1">
          <span className={`rounded border px-1 ${RISK_CLS.green}`}>{riskCounts.green}</span>
          <span className={`rounded border px-1 ${RISK_CLS.amber}`}>{riskCounts.amber}</span>
          <span className={`rounded border px-1 ${RISK_CLS.red}`}>{riskCounts.red}</span>
        </span>
        <div className="ml-auto flex items-center gap-1">
          {live.isFetching && <Spinner label="Refreshing" />}
          <button
            type="button"
            className={btnPrimaryCls}
            disabled={runJob.isPending}
            onClick={() => runJob.mutate({ name: "intraday_monitor", serviceDate: null })}
          >
            {runJob.isPending ? "Starting..." : "Run monitor now"}
          </button>
        </div>
      </div>
      {plans.error && <ErrorBanner error={plans.error} />}
      {plan.error && <ErrorBanner error={plan.error} onRetry={() => void plan.refetch()} />}
      {live.error && <ErrorBanner error={live.error} onRetry={() => void live.refetch()} />}

      <div className="flex flex-col gap-2 xl:flex-row">
        <div className="min-w-0 flex-1 space-y-2">
          {plan.isLoading ? (
            <Spinner label="Loading plan" />
          ) : operative ? (
            <Timeline
              date={date}
              lanes={lanes}
              selectedDriverId={selectedDriverId}
              onSelectDriver={setSelectedDriverId}
              selectedTripId={panelTrip}
              onOpenTrip={(a: Assignment) => setPanelTrip(a.trip_id)}
              onMove={() => toast.info("Moves are made on the Plan page")}
              changedTrips={EMPTY_SET}
              driverDiff={EMPTY_DIFF}
              colorFor={colorFor}
              nowMs={nowMs}
              liveStatus
              laneExtra={laneExtra}
              laneExtraWidth={150}
              busy={resolve.isPending}
              maxHeight="56vh"
            />
          ) : (
            <div className="rounded border border-dashed border-neutral-300 bg-white p-6 text-center text-xs text-neutral-500">
              No plan to monitor. Solve and publish one on the <Link className="underline" to={`/plan?date=${date}`}>Plan page</Link>.
            </div>
          )}
          <div className="grid gap-2 lg:grid-cols-2">
            <RepairFeed repairs={repairs.data ?? []} loading={repairs.isLoading} error={repairs.error} date={date} />
            <SimControl selectedDate={date} isAdmin={isAdmin} />
          </div>
        </div>
        <div className="h-[420px] shrink-0 xl:sticky xl:top-24 xl:h-[calc(100vh-140px)] xl:w-[420px]">
          <Suspense fallback={<div className="flex h-full items-center justify-center rounded border border-neutral-200 bg-white text-xs text-neutral-500">Loading map...</div>}>
            <MapView
              lanes={lanes}
              unassigned={unassigned}
              selectedDriverId={selectedDriverId}
              onSelectDriver={setSelectedDriverId}
              colorFor={colorFor}
              fitKey={operative?.plan_id ?? null}
              live={livePositions}
              showDrops={false}
            />
          </Suspense>
        </div>
      </div>

      <TripPanel
        a={panel?.a ?? null}
        lane={panel?.lane ?? null}
        lanes={lanes}
        onClose={() => setPanelTrip(null)}
        onMove={() => toast.info("Moves are made on the Plan page")}
        onLock={() => toast.info("Locks are changed on the Plan page")}
        busy={false}
        readOnly
      />
    </div>
  );
}

function RepairFeed({ repairs, loading, error, date }: { repairs: Repair[]; loading: boolean; error: unknown; date: string }) {
  const sorted = [...repairs].sort((a, b) => b.created_at.localeCompare(a.created_at)); // newest first; shown at simulated time when present
  return (
    <Section title={<>Repairs <span className="font-normal text-neutral-500">({repairs.length})</span></>}>
      {!!error && <ErrorBanner error={error} />}
      <div className="max-h-72 overflow-y-auto rounded border border-neutral-200 bg-white text-xs">
        {loading && <div className="p-2"><Spinner /></div>}
        {!loading && sorted.length === 0 && <div className="p-3 text-center text-neutral-400">No repairs for this date</div>}
        <ul className="divide-y divide-neutral-100">
          {sorted.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-2 py-1">
              <span className="font-mono text-neutral-500">{fmtTime(typeof r.details?.at === "string" ? r.details.at : r.created_at)}</span>
              <span className="font-medium">{r.trigger.replace(/_/g, " ")}</span>
              <span className={r.outcome === "no_feasible_repair" ? "font-semibold text-red-700" : "text-emerald-700"}>{r.outcome.replace(/_/g, " ")}</span>
              <span className="text-neutral-500">{r.trip_ids.length} trips</span>
              {r.plan_id_after && (
                <Link className="ml-auto text-sky-700 hover:underline" to={`/plan?date=${date}&plan=${r.plan_id_after}`}>
                  open version
                </Link>
              )}
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}

const DISTURBANCES: { key: "slow_legs" | "no_show_driver" | "low_soc" | "late_bookings" | "cancellation"; label: string }[] = [
  { key: "slow_legs", label: "Slow legs" },
  { key: "no_show_driver", label: "No-show driver" },
  { key: "low_soc", label: "Low SOC" },
  { key: "late_bookings", label: "Late bookings" },
  { key: "cancellation", label: "Cancellations" },
];

function SimControl({ selectedDate, isAdmin }: { selectedDate: string; isAdmin: boolean }) {
  const status = useSimStatus();
  const ctl = useSimControl();
  const sim = useSimNow();
  const [simDate, setSimDate] = useState(selectedDate);
  const [speed, setSpeed] = useState("60");
  useEffect(() => setSimDate(selectedDate), [selectedDate]);
  const st = status.data;
  const stats = (st?.stats ?? {}) as Record<string, unknown>;
  const disabled = !isAdmin || ctl.isPending;

  return (
    <Section
      title="Simulator"
      right={
        st && (
          <span className={`flex items-center gap-1 text-[11px] ${st.process_alive ? "text-emerald-700" : "text-amber-700"}`}>
            <span className={`inline-block h-2 w-2 rounded-full ${st.process_alive ? "bg-emerald-500" : "bg-amber-500"}`} />
            {st.process_alive ? "process alive" : "process down"}
          </span>
        )
      }
    >
      <div className="space-y-2 rounded border border-neutral-200 bg-white p-2 text-xs">
        {status.error ? (
          <ErrorBanner error={status.error} onRetry={() => void status.refetch()} />
        ) : !st ? (
          <Spinner />
        ) : (
          <>
            {!st.process_alive && (
              <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-900">
                The simulator process is not running. Start the sim service: <code className="font-mono">docker compose --profile sim up -d sim</code>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <span>
                {st.exists ? (st.running ? "Running" : "Paused") : "No simulation"}
                {st.service_date ? ` for ${st.service_date}` : ""}
              </span>
              {sim.simMs !== null && <span className="font-mono">{fmtTimeSec(sim.simMs)} IST</span>}
              {st.speed != null && <span className="text-neutral-500">x{st.speed}</span>}
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-0.5">
                <span className="text-neutral-500">Service date</span>
                <input type="date" className={inputCls} value={simDate} disabled={disabled} onChange={(e) => setSimDate(e.target.value)} />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-neutral-500">Speed (x)</span>
                <input inputMode="decimal" className={`${inputCls} w-16`} value={speed} disabled={disabled} onChange={(e) => setSpeed(e.target.value)} />
              </label>
              <button
                type="button"
                className={btnPrimaryCls}
                disabled={disabled}
                onClick={() => {
                  const sp = Number(speed);
                  ctl.mutate({ kind: "start", serviceDate: simDate || null, speed: Number.isFinite(sp) && speed.trim() !== "" ? sp : null }, { onSuccess: () => toast.success("Simulator started") });
                }}
              >
                Start
              </button>
              <button type="button" className={btnCls} disabled={disabled || !st.running} onClick={() => ctl.mutate({ kind: "pause" }, { onSuccess: () => toast.info("Simulator paused") })}>
                Pause
              </button>
              <button
                type="button"
                className={btnCls}
                disabled={disabled || !st.exists}
                onClick={() => {
                  if (window.confirm("Reset the simulation? Simulated progress is discarded.")) ctl.mutate({ kind: "reset" }, { onSuccess: () => toast.info("Simulator reset") });
                }}
              >
                Reset
              </button>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {DISTURBANCES.map((d) => {
                const on = !!st.disturbances[d.key];
                return (
                  <label key={d.key} className="flex items-center gap-1">
                    <input type="checkbox" checked={on} disabled={disabled} onChange={(e) => ctl.mutate({ kind: "disturbances", flags: { [d.key]: e.target.checked } })} />
                    {d.label}
                  </label>
                );
              })}
            </div>
            {!isAdmin && <div className="text-[11px] text-neutral-500">Simulator controls require the admin role.</div>}
            {Object.keys(stats).length > 0 && (
              <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 sm:grid-cols-4">
                {Object.entries(stats).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-1">
                    <dt className="text-neutral-500">{k.replace(/_/g, " ")}</dt>
                    <dd className="font-mono">{typeof v === "number" ? fmtNum(v) : String(v)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        )}
      </div>
    </Section>
  );
}
