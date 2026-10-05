import type { ReactNode } from "react";
import type { PlanSummary } from "../state/queries";
import { fmtNum } from "./ui";
import { REASON_LABEL } from "../lib/planStyle";

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function rec(v: unknown): Record<string, number> {
  if (typeof v !== "object" || v === null) return {};
  return Object.fromEntries(Object.entries(v as Record<string, unknown>).filter(([, x]) => typeof x === "number")) as Record<string, number>;
}

function Kpi({ label, children, tone }: { label: string; children: ReactNode; tone?: "warn" }) {
  return (
    <div className={`min-w-[96px] rounded border px-2 py-1 ${tone === "warn" ? "border-amber-300 bg-amber-50" : "border-neutral-200 bg-white"}`}>
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</div>
      <div className="font-mono text-sm font-semibold">{children}</div>
    </div>
  );
}

export function Kpis({ plan }: { plan: PlanSummary }) {
  const k = plan.kpis as Record<string, unknown>;
  const s = plan.solver_stats as Record<string, unknown>;
  const unassigned = num(k.unassigned) ?? 0;
  const bySource = rec(k.estimates_by_source);
  const byReason = rec(k.unassigned_by_reason);
  const solveS = num(s.solve_s);
  return (
    <div className="flex flex-wrap items-stretch gap-1.5 text-xs">
      <Kpi label="Drivers used">{fmtNum(num(k.drivers_used))}</Kpi>
      <Kpi label="Assigned">
        {fmtNum(num(k.assigned))}
        <span className="text-[10px] font-normal text-neutral-500"> / {fmtNum(num(k.trips))}</span>
      </Kpi>
      <Kpi label="Unassigned" tone={unassigned > 0 ? "warn" : undefined}>
        {fmtNum(unassigned)}
      </Kpi>
      <Kpi label="Deadhead min/trip">{fmtNum(num(k.deadhead_min_per_trip), 1)}</Kpi>
      <Kpi label="Buffer h/trip">{fmtNum(num(k.buffer_h_per_trip), 2)}</Kpi>
      <div className="rounded border border-neutral-200 bg-white px-2 py-1">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500">Estimates by source</div>
        <div className="flex flex-wrap gap-x-2 font-mono text-[11px]">
          {Object.keys(bySource).length === 0 ? "-" : Object.entries(bySource).map(([src, n]) => <span key={src}>{src} {n}</span>)}
        </div>
      </div>
      {Object.keys(byReason).length > 0 && (
        <div className="rounded border border-neutral-200 bg-white px-2 py-1">
          <div className="text-[10px] uppercase tracking-wide text-neutral-500">Unassigned by reason</div>
          <div className="flex flex-wrap gap-x-2 font-mono text-[11px]">
            {Object.entries(byReason).map(([r, n]) => (
              <span key={r}>
                {REASON_LABEL[r] ?? r} {n}
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="rounded border border-neutral-200 bg-white px-2 py-1">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500">Solver</div>
        <div className="font-mono text-[11px]">
          {typeof s.stop_reason === "string" ? s.stop_reason : "-"}
          {solveS !== null ? `, ${fmtNum(solveS, 1)} s` : ""}
        </div>
      </div>
    </div>
  );
}
