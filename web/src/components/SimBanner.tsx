import { useEffect, useState } from "react";
import { useSimClock } from "../state/sse";
import { fmtDateTime, fmtTimeSec } from "../lib/time";

const FRESH_MS = 10_000;

/** Shows while sim.clock events are arriving (one in the last 10 s). */
export function SimBanner() {
  const clock = useSimClock();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!clock) return;
    const h = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(h);
  }, [clock]);
  if (!clock || now - clock.receivedAt > FRESH_MS) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 bg-violet-700 px-3 py-1 text-xs text-white">
      <span className="font-semibold">
        Simulation {clock.running ? "running" : "paused"}: {fmtTimeSec(clock.sim_time)} IST
      </span>
      <span className="opacity-80">({fmtDateTime(clock.sim_time)})</span>
      <span className="opacity-80">speed x{clock.speed}</span>
      {clock.service_date && <span className="opacity-80">service date {clock.service_date}</span>}
    </div>
  );
}
