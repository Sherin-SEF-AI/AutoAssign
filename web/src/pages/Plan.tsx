import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../state/auth";
import { useSearchParam } from "../state/date";
import { useSelectedPlan } from "../state/plan";
import {
  adoptPlan,
  useAcknowledge,
  useLock,
  useMove,
  usePlan,
  usePlanDiff,
  usePublish,
  useRepairs,
  useSolve,
  type Assignment,
  type DriverLane,
  type MoveIn,
  type MoveOut,
  type PlanSummary,
} from "../state/queries";
import { asApiError } from "../api/errors";
import { fmtDateTime, fmtMs } from "../lib/time";
import { driverColor } from "../lib/planStyle";
import { ErrorBanner, toast } from "../components/Toast";
import { Kpis } from "../components/Kpis";
import { Timeline, type LaneDiff } from "../components/Timeline";
import { UnassignedTray } from "../components/UnassignedTray";
import { TripPanel } from "../components/TripPanel";
import { MovePanel } from "../components/MovePanel";
import { Modal } from "../components/Drawer";
import { Spinner, StatusChip, btnCls, btnPrimaryCls, inputCls } from "../components/ui";

const MapView = lazy(() => import("../components/MapView"));

const MAP_W_KEY = "blurabbit.plan.mapWidth";

function readMapWidth(): number {
  try {
    const v = Number(window.localStorage.getItem(MAP_W_KEY));
    return Number.isFinite(v) && v >= 280 ? v : 440;
  } catch {
    return 440;
  }
}

function SolveButton({ date, primary, onSolved }: { date: string; primary?: boolean; onSolved: (p: PlanSummary) => void }) {
  const solve = useSolve();
  const [started, setStarted] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!solve.isPending) return;
    const h = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(h);
  }, [solve.isPending]);
  return (
    <button
      type="button"
      className={primary ? btnPrimaryCls : btnCls}
      disabled={solve.isPending}
      title="Solve the whole date from scratch (can take up to a minute)"
      onClick={() => {
        setStarted(Date.now());
        solve.mutate(date, {
          onSuccess: (p) => {
            toast.success(`Solved: v${p.version}`, `${date}, status ${p.status}`);
            onSolved(p);
          },
        });
      }}
    >
      {solve.isPending ? `Solving... ${started ? fmtMs(now - started) : ""}` : "Solve date"}
    </button>
  );
}

export default function PlanPage() {
  const { role } = useAuth();
  // Plan edits are allowed for both ops and admin.
  const readOnly = role !== "admin" && role !== "ops";
  const qc = useQueryClient();
  const { date, plans, list, planId, summary, setPlanId } = useSelectedPlan();
  const plan = usePlan(planId);
  const detail = plan.data;
  const current = detail?.plan ?? summary;
  const repairs = useRepairs(date);

  const [selectedDriverId, setSelectedDriverId] = useState<string | null>(null);
  const [panel, setPanel] = useState<{ tripId: string } | null>(null);
  const [moveResult, setMoveResult] = useState<{ result: MoveOut; body: MoveIn; planId: string } | null>(null);
  const [ackDialog, setAckDialog] = useState<{ tripIds: string[] } | null>(null);
  const [ackNote, setAckNote] = useState("");
  const [diffOn, setDiffOn] = useSearchParam("diff");
  const [againstParam, setAgainst] = useSearchParam("against");
  const [mapW, setMapW] = useState(readMapWidth);

  const move = useMove();
  const lock = useLock();
  const publish = usePublish();
  const ack = useAcknowledge();

  const lanes = useMemo<DriverLane[]>(() => {
    const ls = [...(detail?.drivers ?? [])];
    ls.sort((a, b) => Number(b.available) - Number(a.available) || (a.shift_start_at ?? "").localeCompare(b.shift_start_at ?? "") || a.name.localeCompare(b.name));
    return ls;
  }, [detail]);
  const unassigned = detail?.unassigned ?? [];
  const lanesById = useMemo(() => new Map(lanes.map((l) => [l.driver_id, l])), [lanes]);
  const colorIndex = useMemo(() => new Map([...lanes].sort((a, b) => a.driver_id.localeCompare(b.driver_id)).map((l, i) => [l.driver_id, i])), [lanes]);
  const colorFor = useCallback((id: string) => driverColor(id, colorIndex.get(id)), [colorIndex]);

  // Diff against another version (default: parent, else the next older version).
  const defaultAgainst = current?.parent_plan_id ?? list.find((p) => current && p.version < current.version)?.plan_id ?? null;
  const against = diffOn ? (againstParam && againstParam !== planId ? againstParam : defaultAgainst) : null;
  const diff = usePlanDiff(planId, against);
  const changedTrips = useMemo(() => new Set((diff.data?.trips ?? []).filter((t) => t.changed).map((t) => t.trip_id)), [diff.data]);
  const driverDiff = useMemo(() => new Map<string, LaneDiff>((diff.data?.drivers ?? []).map((d) => [d.driver_id, { gained: d.gained, lost: d.lost }])), [diff.data]);

  // Find the trip for the detail panel in the current version.
  const panelTrip = useMemo(() => {
    if (!panel) return null;
    for (const l of lanes) {
      const a = l.assignments.find((x) => x.trip_id === panel.tripId);
      if (a) return { a, lane: l as DriverLane | null };
    }
    const u = unassigned.find((x) => x.trip_id === panel.tripId);
    return u ? { a: u, lane: null } : null;
  }, [panel, lanes, unassigned]);

  const switchTo = useCallback(
    (p: PlanSummary) => {
      adoptPlan(qc, p);
      setPlanId(p.plan_id);
    },
    [qc, setPlanId],
  );

  const doMove = useCallback(
    (body: MoveIn) => {
      if (!planId) return;
      const fromPlan = planId;
      move.mutate(
        { planId: fromPlan, body },
        {
          onSuccess: (result) => {
            if (result.accepted) {
              if (result.plan) switchTo(result.plan);
              toast.success(result.forced ? "Move forced" : "Move applied", result.message);
              setMoveResult(result.warnings.length > 0 ? { result, body, planId: fromPlan } : null);
            } else {
              setMoveResult({ result, body, planId: fromPlan });
            }
          },
        },
      );
    },
    [planId, move, switchTo],
  );

  const onMove = useCallback(
    (tripId: string, toDriverId: string | null) => {
      if (readOnly) return;
      doMove({ trip_id: tripId, to_driver_id: toDriverId, reason: "", force: false, dry_run: false });
    },
    [doMove, readOnly],
  );

  const onLock = (tripId: string, lockIt: boolean) => {
    if (!planId) return;
    lock.mutate({ planId, tripId, lock: lockIt }, { onSuccess: (p) => {
      setPlanId(p.plan_id);
      toast.success(lockIt ? "Locked" : "Unlocked", `new version v${p.version}`);
    } });
  };

  const doPublish = () => {
    if (!planId) return;
    publish.mutate(planId, {
      onSuccess: (p) => toast.success(`Published v${p.version}`),
      onError: (e) => {
        const err = asApiError(e);
        if (err.code === "unassigned_not_acknowledged") {
          const ids = Array.isArray(err.extra.trip_ids) ? err.extra.trip_ids.map(String) : unassigned.filter((a) => !a.acknowledged).map((a) => a.trip_id);
          setAckDialog({ tripIds: ids });
        } else {
          toast.error(err, "Publish");
        }
      },
    });
  };

  const ackAndPublish = async () => {
    if (!planId || !ackDialog) return;
    try {
      await ack.mutateAsync({ planId, tripIds: ackDialog.tripIds, note: ackNote.trim() || "acknowledged before publish" });
      setAckDialog(null);
      doPublish();
    } catch {
      // the mutation cache already showed the error
    }
  };

  const onResizeStart = (e: ReactPointerEvent) => {
    e.preventDefault();
    const sx = e.clientX;
    const sw = mapW;
    const moveH = (ev: PointerEvent) => setMapW(Math.max(280, Math.min(window.innerWidth * 0.6, sw - (ev.clientX - sx))));
    const up = () => {
      window.removeEventListener("pointermove", moveH);
      window.removeEventListener("pointerup", up);
      setMapW((w) => {
        try {
          window.localStorage.setItem(MAP_W_KEY, String(Math.round(w)));
        } catch {
          // ignore storage failures
        }
        return w;
      });
    };
    window.addEventListener("pointermove", moveH);
    window.addEventListener("pointerup", up);
  };

  // Clear driver selection when the plan changes date.
  const lastDate = useRef(date);
  useEffect(() => {
    if (lastDate.current !== date) {
      setSelectedDriverId(null);
      setPanel(null);
      lastDate.current = date;
    }
  }, [date]);

  /* ---------- empty / loading states ---------- */

  if (plans.isLoading) return <Spinner label="Loading plans" />;
  if (plans.error) {
    return (
      <div className="space-y-2">
        <ErrorBanner error={plans.error} onRetry={() => void plans.refetch()} />
        <SolveButton date={date} primary onSolved={switchTo} />
      </div>
    );
  }
  if (list.length === 0 || !planId) {
    return (
      <div className="rounded border border-dashed border-neutral-300 bg-white p-8 text-center text-xs">
        <div className="mb-1 text-sm font-semibold">No plan for {date}</div>
        <div className="mb-3 text-neutral-500">Run the solver to create the first version for this date (takes up to a minute).</div>
        <SolveButton date={date} primary onSolved={switchTo} />
      </div>
    );
  }

  const repairCount = repairs.data?.length ?? 0;
  const canPublish = !!current && current.status === "draft";
  const busy = move.isPending || lock.isPending;

  return (
    <div className="space-y-2">
      {/* header */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <h1 className="text-sm font-semibold">Plan {date}</h1>
        {current && (
          <>
            <span className="font-mono">v{current.version}</span>
            <StatusChip status={current.status} />
            <span className="text-neutral-500" title={current.reason}>
              {current.trigger} by {current.created_by}, {fmtDateTime(current.created_at)}
              {current.published_at ? `, published ${fmtDateTime(current.published_at)}` : ""}
            </span>
          </>
        )}
        {repairCount > 0 && <span className="rounded border border-violet-200 bg-violet-50 px-1 text-[11px] text-violet-800">{repairCount} repairs today</span>}
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={!!diffOn} onChange={(e) => setDiffOn(e.target.checked ? "1" : null)} />
            Compare with
          </label>
          <select
            aria-label="Compare against version"
            className={inputCls}
            disabled={!diffOn}
            value={against ?? ""}
            onChange={(e) => setAgainst(e.target.value || null)}
          >
            {!against && <option value="">none</option>}
            {list
              .filter((p) => p.plan_id !== planId)
              .map((p) => (
                <option key={p.plan_id} value={p.plan_id}>
                  v{p.version} {p.status}
                  {p.plan_id === current?.parent_plan_id ? " (parent)" : ""}
                </option>
              ))}
          </select>
          {diffOn && diff.data && <span className="text-amber-700">{diff.data.changed_trips} changed trips</span>}
          {diffOn && diff.isFetching && <Spinner label="Diffing" />}
          <SolveButton date={date} onSolved={switchTo} />
          <button
            type="button"
            className={btnPrimaryCls}
            disabled={!canPublish || publish.isPending || readOnly}
            title={canPublish ? "Publish this version" : `Cannot publish a ${current?.status ?? "missing"} version`}
            onClick={doPublish}
          >
            {publish.isPending ? "Publishing..." : current?.status === "published" ? "Published" : "Publish"}
          </button>
        </div>
      </div>
      {diffOn && diff.error && <ErrorBanner error={diff.error} />}
      {plan.error && <ErrorBanner error={plan.error} onRetry={() => void plan.refetch()} />}
      {current && <Kpis plan={current} />}

      {/* timeline + map */}
      <div className="flex flex-col gap-2 xl:flex-row">
        <div className="min-w-0 flex-1 space-y-2">
          {plan.isLoading ? (
            <Spinner label="Loading plan" />
          ) : (
            <Timeline
              date={date}
              lanes={lanes}
              selectedDriverId={selectedDriverId}
              onSelectDriver={setSelectedDriverId}
              selectedTripId={panel?.tripId ?? null}
              onOpenTrip={(a: Assignment) => setPanel({ tripId: a.trip_id })}
              onMove={onMove}
              changedTrips={changedTrips}
              driverDiff={driverDiff}
              colorFor={colorFor}
              busy={busy || (plan.isFetching && plan.isPlaceholderData)}
            />
          )}
          <Legend />
          <UnassignedTray
            items={unassigned}
            onOpen={(a) => setPanel({ tripId: a.trip_id })}
            onMove={onMove}
            onAcknowledgeAll={(note) => planId && ack.mutate({ planId, tripIds: null, note: note || "acknowledged from plan page" }, { onSuccess: (o) => toast.success(`Acknowledged ${o.acknowledged} trips`) })}
            ackBusy={ack.isPending}
            readOnly={readOnly}
          />
        </div>
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize map"
          onPointerDown={onResizeStart}
          className="hidden w-1.5 shrink-0 cursor-col-resize rounded bg-neutral-200 hover:bg-sky-300 xl:block"
        />
        <div className="h-[420px] shrink-0 xl:sticky xl:top-24 xl:h-[calc(100vh-140px)]">
          <div className="h-full w-full xl:w-[var(--map-w)]" style={{ ["--map-w" as string]: `${mapW}px` }}>
            <Suspense fallback={<div className="flex h-full items-center justify-center rounded border border-neutral-200 bg-white text-xs text-neutral-500">Loading map...</div>}>
              <MapView lanes={lanes} unassigned={unassigned} selectedDriverId={selectedDriverId} onSelectDriver={setSelectedDriverId} colorFor={colorFor} fitKey={planId} />
            </Suspense>
          </div>
        </div>
      </div>

      <TripPanel
        a={panelTrip?.a ?? null}
        lane={panelTrip?.lane ?? null}
        lanes={lanes}
        onClose={() => setPanel(null)}
        onMove={onMove}
        onLock={onLock}
        busy={busy}
        readOnly={readOnly}
      />
      <MovePanel
        result={moveResult?.result ?? null}
        lanesById={lanesById}
        movedTripId={moveResult?.body.trip_id ?? null}
        busy={move.isPending}
        onClose={() => setMoveResult(null)}
        onForce={(reason) => moveResult && doMove({ ...moveResult.body, force: true, reason })}
      />
      <Modal open={!!ackDialog} title="Unassigned trips need acknowledgement" onClose={() => setAckDialog(null)}>
        <div>
          {ackDialog?.tripIds.length ?? 0} unassigned trips have not been acknowledged. Acknowledge them with a note, then publish.
        </div>
        <input className={`${inputCls} w-full py-1`} placeholder="Note (why these stay unassigned)" value={ackNote} onChange={(e) => setAckNote(e.target.value)} />
        {ack.error && <ErrorBanner error={ack.error} />}
        <div className="flex justify-end gap-1">
          <button type="button" className={btnCls} onClick={() => setAckDialog(null)}>
            Cancel
          </button>
          <button type="button" className={btnPrimaryCls} disabled={ack.isPending || publish.isPending} onClick={() => void ackAndPublish()}>
            {ack.isPending ? "Acknowledging..." : "Acknowledge and publish"}
          </button>
        </div>
      </Modal>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 text-[10px] text-neutral-600">
      <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-4 bg-neutral-500" />deadhead</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 border border-amber-500" style={{ background: "repeating-linear-gradient(45deg,#fef3c7 0 2px,#d97706 2px 3px)" }} />buffer</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-blue-600" />ets</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-violet-600" />airport</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-emerald-600" />city</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-orange-600" />package</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-red-600" />VIP</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm border-2 border-amber-600" />changed vs compared version</span>
      <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 bg-sky-100" />shift</span>
      <span className="text-neutral-400">Drag a trip to another lane to move it. Keyboard: Tab to a trip, Space to pick up, arrows to choose a lane, Enter to drop.</span>
    </div>
  );
}

