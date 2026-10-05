import { useEffect, useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { eventStreamUrl } from "../api/client";
import { toast } from "../components/Toast";

export const SSE_EVENTS = [
  "plan.drafted",
  "plan.published",
  "plan.repaired",
  "plan.updated",
  "monitor.tick",
  "sim.clock",
  "budget.warning",
  "job.finished",
  "data.regenerated",
] as const;

export type SseEventName = (typeof SSE_EVENTS)[number];

export interface SseEnvelope<T = unknown> {
  event: string;
  data: T;
  emitted_at?: string;
}

/* ---------------- sim clock store ---------------- */

export interface SimClock {
  sim_time: string;
  running: boolean;
  speed: number;
  service_date: string;
  receivedAt: number;
}

let simClock: SimClock | null = null;
const simListeners = new Set<() => void>();

function setSimClock(v: SimClock) {
  simClock = v;
  simListeners.forEach((l) => l());
}

export function useSimClock(): SimClock | null {
  return useSyncExternalStore(
    (cb) => {
      simListeners.add(cb);
      return () => {
        simListeners.delete(cb);
      };
    },
    () => simClock,
    () => simClock,
  );
}

/* ---------------- connection status (for debugging/indicators) ---------------- */

export type SseStatus = "idle" | "connecting" | "open" | "retrying";
let status: SseStatus = "idle";
const statusListeners = new Set<() => void>();
function setStatus(s: SseStatus) {
  status = s;
  statusListeners.forEach((l) => l());
}
export function useSseStatus(): SseStatus {
  return useSyncExternalStore(
    (cb) => {
      statusListeners.add(cb);
      return () => {
        statusListeners.delete(cb);
      };
    },
    () => status,
    () => status,
  );
}

/* ---------------- dispatch ---------------- */

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function handleEvent(qc: QueryClient, name: string, data: unknown): void {
  if (name.startsWith("plan.")) {
    void qc.invalidateQueries({ queryKey: ["plans"] });
    void qc.invalidateQueries({ queryKey: ["plan"] });
    void qc.invalidateQueries({ queryKey: ["trips"] });
    void qc.invalidateQueries({ queryKey: ["fleet", "drivers"] });
    void qc.invalidateQueries({ queryKey: ["repairs"] });
    return;
  }
  switch (name) {
    case "monitor.tick":
      void qc.invalidateQueries({ queryKey: ["live"] });
      void qc.invalidateQueries({ queryKey: ["repairs"] });
      return;
    case "job.finished":
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["snapshots"] });
      void qc.invalidateQueries({ queryKey: ["estimates"] });
      void qc.invalidateQueries({ queryKey: ["budget"] });
      return;
    case "data.regenerated":
      void qc.invalidateQueries();
      return;
    case "budget.warning": {
      void qc.invalidateQueries({ queryKey: ["budget"] });
      const msg = isObj(data) && typeof data.message === "string" ? data.message : undefined;
      toast.warning("Provider budget warning", msg);
      return;
    }
    case "sim.clock": {
      if (isObj(data) && typeof data.sim_time === "string") {
        setSimClock({
          sim_time: data.sim_time,
          running: data.running !== false,
          speed: typeof data.speed === "number" ? data.speed : 1,
          service_date: typeof data.service_date === "string" ? data.service_date : "",
          receivedAt: Date.now(),
        });
      }
      return;
    }
    default:
      return;
  }
}

/**
 * Single EventSource for the whole app. The stream may not exist yet: failures are silent and we
 * reconnect with exponential backoff (1 s up to 60 s, with jitter).
 */
export function useEventStream(qc: QueryClient, token: string | null): void {
  useEffect(() => {
    if (!token || typeof EventSource === "undefined") return;
    let es: EventSource | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let stopped = false;

    const onMessage = (fallbackName: string) => (ev: MessageEvent<string>) => {
      let env: unknown;
      try {
        env = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (isObj(env)) {
        const name = typeof env.event === "string" ? env.event : fallbackName;
        handleEvent(qc, name, "data" in env ? env.data : env);
      }
    };

    const connect = () => {
      if (stopped) return;
      setStatus(attempt === 0 ? "connecting" : "retrying");
      try {
        es = new EventSource(eventStreamUrl(token));
      } catch {
        schedule();
        return;
      }
      es.onopen = () => {
        attempt = 0;
        setStatus("open");
      };
      es.onerror = () => {
        es?.close();
        es = null;
        schedule();
      };
      es.addEventListener("message", onMessage("message") as EventListener);
      for (const name of SSE_EVENTS) es.addEventListener(name, onMessage(name) as EventListener);
    };

    const schedule = () => {
      if (stopped) return;
      setStatus("retrying");
      const base = Math.min(60_000, 1000 * 2 ** attempt);
      attempt = Math.min(attempt + 1, 10);
      timer = window.setTimeout(connect, base / 2 + Math.random() * (base / 2));
    };

    connect();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      es?.close();
      setStatus("idle");
    };
  }, [qc, token]);
}
