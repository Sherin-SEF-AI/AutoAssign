import { useState, type ReactNode, type ThHTMLAttributes, type TdHTMLAttributes } from "react";

/* Table helpers: dense, sticky header, horizontal scroll on narrow screens. */

export function TableWrap({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`overflow-auto rounded border border-neutral-200 bg-white ${className}`}>
      <table className="min-w-full border-collapse text-xs">{children}</table>
    </div>
  );
}

export function Th({ children, className = "", ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      {...rest}
      className={`sticky top-0 z-[1] whitespace-nowrap border-b border-neutral-200 bg-neutral-50 px-2 py-1.5 text-left font-medium text-neutral-600 ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({ children, className = "", ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td {...rest} className={`border-b border-neutral-100 px-2 py-1 align-top ${className}`}>
      {children}
    </td>
  );
}

export function EmptyRow({ cols, children = "No rows" }: { cols: number; children?: ReactNode }) {
  return (
    <tr>
      <td colSpan={cols} className="px-2 py-4 text-center text-neutral-500">
        {children}
      </td>
    </tr>
  );
}

export function Section({ title, right, children, className = "" }: { title: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`space-y-2 ${className}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-neutral-800">{title}</h2>
        <div className="ml-auto flex flex-wrap items-center gap-2">{right}</div>
      </div>
      {children}
    </section>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="text-xs text-neutral-500">{label}...</span>;
}

const STATUS_CLS: Record<string, string> = {
  // trip lifecycle
  booked: "bg-neutral-100 text-neutral-700 border-neutral-300",
  assigned: "bg-sky-50 text-sky-800 border-sky-200",
  en_route: "bg-indigo-50 text-indigo-800 border-indigo-200",
  arrived: "bg-violet-50 text-violet-800 border-violet-200",
  started: "bg-blue-50 text-blue-800 border-blue-200",
  completed: "bg-emerald-50 text-emerald-800 border-emerald-200",
  cancelled: "bg-neutral-100 text-neutral-500 border-neutral-300 line-through",
  no_show: "bg-orange-50 text-orange-800 border-orange-200",
  // jobs
  queued: "bg-neutral-100 text-neutral-700 border-neutral-300",
  running: "bg-sky-50 text-sky-800 border-sky-200",
  succeeded: "bg-emerald-50 text-emerald-800 border-emerald-200",
  failed: "bg-red-50 text-red-800 border-red-200",
  skipped: "bg-amber-50 text-amber-800 border-amber-200",
  // plans
  draft: "bg-amber-50 text-amber-800 border-amber-200",
  published: "bg-emerald-50 text-emerald-800 border-emerald-200",
  superseded: "bg-neutral-100 text-neutral-500 border-neutral-300",
  // vehicles
  active: "bg-emerald-50 text-emerald-800 border-emerald-200",
  maintenance: "bg-amber-50 text-amber-800 border-amber-200",
  inactive: "bg-neutral-100 text-neutral-500 border-neutral-300",
};

export function StatusChip({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-neutral-400">-</span>;
  const cls = STATUS_CLS[status] ?? "bg-neutral-50 text-neutral-700 border-neutral-200";
  return <span className={`inline-block whitespace-nowrap rounded border px-1.5 py-px text-[11px] ${cls}`}>{status}</span>;
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "red" | "amber" | "blue" | "green"; title?: string }) {
  const cls = {
    neutral: "bg-neutral-100 text-neutral-700 border-neutral-200",
    red: "bg-red-50 text-red-800 border-red-200",
    amber: "bg-amber-50 text-amber-800 border-amber-200",
    blue: "bg-sky-50 text-sky-800 border-sky-200",
    green: "bg-emerald-50 text-emerald-800 border-emerald-200",
  }[tone];
  return (
    <span title={title} className={`inline-block whitespace-nowrap rounded border px-1 py-px text-[10px] font-medium ${cls}`}>
      {children}
    </span>
  );
}

/** Collapsible pretty-printed JSON. */
export function JsonBlock({ value, label = "json", defaultOpen = false }: { value: unknown; label?: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const isEmpty = value === null || value === undefined || (typeof value === "object" && Object.keys(value as object).length === 0);
  if (isEmpty) return <span className="text-neutral-400">{"{}"}</span>;
  return (
    <div>
      <button type="button" className="text-[11px] text-sky-700 hover:underline" onClick={() => setOpen((o) => !o)}>
        {open ? "hide" : "show"} {label}
      </button>
      {open && <pre className="mt-1 max-h-64 max-w-[480px] overflow-auto rounded bg-neutral-50 p-1.5 font-mono text-[11px]">{JSON.stringify(value, null, 2)}</pre>}
    </div>
  );
}

export function Placeholder({ title, note = "Coming in a later milestone" }: { title: string; note?: string }) {
  return (
    <div className="rounded border border-dashed border-neutral-300 bg-white p-6 text-center">
      <div className="text-sm font-semibold text-neutral-700">{title}</div>
      <div className="mt-1 text-xs text-neutral-500">{note}</div>
    </div>
  );
}

export function fmtNum(v: number | null | undefined, digits = 0, fallback = "-"): string {
  if (v === null || v === undefined || Number.isNaN(v)) return fallback;
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export const inputCls =
  "rounded border border-neutral-300 bg-white px-1.5 py-0.5 text-xs focus:border-sky-500 focus:outline-none disabled:bg-neutral-100 disabled:text-neutral-400";
export const btnCls =
  "rounded border border-neutral-300 bg-white px-2 py-0.5 text-xs hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50";
export const btnPrimaryCls =
  "rounded border border-neutral-800 bg-neutral-800 px-2 py-0.5 text-xs text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-50";
