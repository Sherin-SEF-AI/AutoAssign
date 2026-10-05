import { useEffect, useState } from "react";
import { useSimClock } from "./sse";
import { useSimStatus } from "./queries";

const SSE_FRESH_MS = 10_000;

export interface SimNow {
  exists: boolean;
  running: boolean;
  speed: number;
  serviceDate: string | null;
  /** current simulated instant (ms), extrapolated between updates while running */
  simMs: number | null;
  processAlive: boolean | null;
  source: "sse" | "poll" | "none";
}

/**
 * Simulator clock from sim.clock SSE events when fresh, else from polling /admin/sim/status (every 5 s).
 * Ticks once a second so callers can render a moving clock.
 */
export function useSimNow(): SimNow {
  const clock = useSimClock();
  const status = useSimStatus();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const h = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(h);
  }, []);

  const st = status.data;
  const sseFresh = !!clock && now - clock.receivedAt <= SSE_FRESH_MS;
  if (sseFresh && clock) {
    const base = Date.parse(clock.sim_time);
    const simMs = Number.isNaN(base) ? null : clock.running ? base + (now - clock.receivedAt) * clock.speed : base;
    return {
      exists: true,
      running: clock.running,
      speed: clock.speed,
      serviceDate: clock.service_date || st?.service_date || null,
      simMs,
      processAlive: st?.process_alive ?? null,
      source: "sse",
    };
  }
  if (st && st.exists) {
    const base = st.sim_time ? Date.parse(st.sim_time) : NaN;
    const speed = st.speed ?? 1;
    const simMs = Number.isNaN(base) ? null : st.running ? base + (now - status.dataUpdatedAt) * speed : base;
    return { exists: true, running: st.running, speed, serviceDate: st.service_date, simMs, processAlive: st.process_alive, source: "poll" };
  }
  return { exists: false, running: false, speed: 1, serviceDate: null, simMs: null, processAlive: st?.process_alive ?? null, source: "none" };
}
