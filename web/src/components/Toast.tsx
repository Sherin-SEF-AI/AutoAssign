import { useEffect, useSyncExternalStore } from "react";
import { ApiError, asApiError } from "../api/errors";

export type ToastKind = "error" | "success" | "info" | "warning";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  message?: string;
  details?: string[];
  ttlMs: number;
}

let items: ToastItem[] = [];
let seq = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function push(t: Omit<ToastItem, "id" | "ttlMs"> & { ttlMs?: number }): number {
  const id = ++seq;
  const ttlMs = t.ttlMs ?? (t.kind === "error" ? 12000 : 5000);
  items = [...items, { ...t, id, ttlMs }].slice(-6);
  emit();
  return id;
}

function dismiss(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

export const toast = {
  success: (title: string, message?: string) => push({ kind: "success", title, message }),
  info: (title: string, message?: string) => push({ kind: "info", title, message }),
  warning: (title: string, message?: string) => push({ kind: "warning", title, message }),
  /** Show an error with its stable code and message. */
  error: (e: unknown, context?: string) => {
    const err: ApiError = asApiError(e);
    return push({
      kind: "error",
      title: `${context ? `${context}: ` : ""}${err.code}${err.status ? ` (${err.status})` : ""}`,
      message: err.detail,
      details: err.errors.map((f) => (f.field ? `${f.field}: ${f.message}` : f.message)),
    });
  },
  dismiss,
};

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

const KIND_CLS: Record<ToastKind, string> = {
  error: "border-red-300 bg-red-50 text-red-900",
  success: "border-emerald-300 bg-emerald-50 text-emerald-900",
  info: "border-neutral-300 bg-white text-neutral-900",
  warning: "border-amber-300 bg-amber-50 text-amber-900",
};

function ToastCard({ t }: { t: ToastItem }) {
  useEffect(() => {
    const h = window.setTimeout(() => dismiss(t.id), t.ttlMs);
    return () => window.clearTimeout(h);
  }, [t.id, t.ttlMs]);
  return (
    <div role={t.kind === "error" ? "alert" : "status"} className={`pointer-events-auto w-80 max-w-full rounded border px-3 py-2 text-xs shadow ${KIND_CLS[t.kind]}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="font-semibold break-words font-mono">{t.title}</div>
          {t.message && <div className="mt-0.5 break-words">{t.message}</div>}
          {t.details && t.details.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {t.details.map((d, i) => (
                <li key={i} className="break-words">{d}</li>
              ))}
            </ul>
          )}
        </div>
        <button type="button" onClick={() => dismiss(t.id)} className="text-neutral-500 hover:text-neutral-900" aria-label="Dismiss">
          x
        </button>
      </div>
    </div>
  );
}

export function Toaster() {
  const list = useSyncExternalStore(subscribe, () => items, () => items);
  return (
    <div className="pointer-events-none fixed bottom-3 right-3 z-[100] flex flex-col gap-2">
      {list.map((t) => (
        <ToastCard key={t.id} t={t} />
      ))}
    </div>
  );
}

/** Inline error banner for failed queries and forms. Shows the error code and message. */
export function ErrorBanner({ error, onRetry, className = "" }: { error: unknown; onRetry?: () => void; className?: string }) {
  if (!error) return null;
  const e = asApiError(error);
  return (
    <div role="alert" className={`rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-900 ${className}`}>
      <span className="font-mono font-semibold">{e.code}</span>
      {e.status ? <span className="text-red-700"> ({e.status})</span> : null}
      <span>: {e.detail}</span>
      {e.errors.length > 0 && (
        <ul className="mt-1 list-disc pl-4">
          {e.errors.map((f, i) => (
            <li key={i}>{f.field ? `${f.field}: ${f.message}` : f.message}</li>
          ))}
        </ul>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry} className="ml-2 underline">
          Retry
        </button>
      )}
    </div>
  );
}
