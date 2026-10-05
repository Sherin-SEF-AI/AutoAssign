import type { JobRun } from "../state/queries";
import { JOB_TERMINAL } from "../state/queries";
import { fmtDateTime, fmtDuration } from "../lib/time";
import { JsonBlock, StatusChip } from "./ui";

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Progress bar from stats.done / stats.total plus final stats when finished. */
export function JobProgress({ run }: { run: JobRun }) {
  const stats = run.stats as Record<string, unknown>;
  const done = num(stats.done);
  const total = num(stats.total);
  const finished = JOB_TERMINAL.has(run.status);
  const pct = finished && run.status === "succeeded" ? 100 : done !== null && total ? Math.min(100, (done / total) * 100) : null;
  const barColor = run.status === "failed" ? "bg-red-500" : run.status === "succeeded" ? "bg-emerald-500" : "bg-sky-500";
  return (
    <div className="space-y-1.5 rounded border border-neutral-200 bg-white p-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono">{run.job_name}</span>
        <StatusChip status={run.status} />
        <span className="text-neutral-500">started {fmtDateTime(run.started_at)}</span>
        <span className="text-neutral-500">{fmtDuration(run.started_at, run.finished_at ?? new Date().toISOString())}</span>
        {typeof stats.day === "string" && !finished && <span className="text-neutral-500">day {stats.day}</span>}
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-neutral-200">
        <div
          className={`h-full ${barColor} ${pct === null ? "w-1/3 animate-pulse" : ""} transition-[width]`}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
      <div className="text-neutral-600">
        {done !== null && total !== null ? `${done} / ${total}` : finished ? "" : "waiting for progress"}
        {pct !== null ? ` (${Math.round(pct)}%)` : ""}
      </div>
      {run.error && <div className="rounded bg-red-50 p-1.5 font-mono text-red-800">{run.error}</div>}
      {finished && <JsonBlock value={run.stats} label="result stats" defaultOpen />}
    </div>
  );
}
