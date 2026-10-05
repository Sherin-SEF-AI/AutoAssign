import { useSimNow } from "../state/sim";
import { fmtDateTime, fmtTimeSec } from "../lib/time";

/** Persistent simulator banner: shown whenever a simulation exists (SSE sim.clock or the status poll). */
export function SimBanner() {
  const sim = useSimNow();
  if (!sim.exists) return null;
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1 text-xs text-white ${sim.running ? "bg-violet-700" : "bg-neutral-600"}`}>
      <span className="font-semibold">
        Simulation {sim.running ? "running" : "paused"}: {sim.simMs !== null ? `${fmtTimeSec(sim.simMs)} IST` : "time unknown"}
      </span>
      {sim.simMs !== null && <span className="opacity-80">({fmtDateTime(sim.simMs)})</span>}
      <span className="opacity-80">speed x{sim.speed}</span>
      {sim.serviceDate && <span className="opacity-80">service date {sim.serviceDate}</span>}
      {sim.processAlive === false && <span className="rounded bg-amber-400 px-1 text-[11px] text-neutral-900">sim process not running</span>}
    </div>
  );
}
