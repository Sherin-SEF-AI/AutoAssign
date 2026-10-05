// All timestamps from the API are UTC ISO strings. Everything shown to users is Asia/Kolkata.

export const TZ = "Asia/Kolkata";

const hhmm = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const hhmmss = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

const dayMonth = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ,
  day: "2-digit",
  month: "short",
});

const ymdParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

type Input = string | number | Date | null | undefined;

function toDate(v: Input): Date | null {
  if (v === null || v === undefined || v === "") return null;
  const d = v instanceof Date ? v : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "HH:mm" in IST, or the fallback for empty/invalid input. */
export function fmtTime(v: Input, fallback = "-"): string {
  const d = toDate(v);
  return d ? hhmm.format(d) : fallback;
}

/** "HH:mm:ss" in IST. */
export function fmtTimeSec(v: Input, fallback = "-"): string {
  const d = toDate(v);
  return d ? hhmmss.format(d) : fallback;
}

/** "05 Oct HH:mm" in IST. */
export function fmtDateTime(v: Input, fallback = "-"): string {
  const d = toDate(v);
  return d ? `${dayMonth.format(d)} ${hhmm.format(d)}` : fallback;
}

/** YYYY-MM-DD of the given instant as seen in IST. */
export function istDate(v: Input = new Date()): string {
  const d = toDate(v) ?? new Date();
  return ymdParts.format(d);
}

/** Add whole days to a YYYY-MM-DD string (calendar arithmetic, timezone independent). */
export function addDays(ymd: string, days: number): string {
  const [y, m, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

export function todayIST(): string {
  return istDate(new Date());
}

export function tomorrowIST(): string {
  return addDays(todayIST(), 1);
}

export function isYmd(s: string | null | undefined): s is string {
  return !!s && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s));
}

/** Seconds to whole minutes, for estimate display. */
export function secToMin(s: number | null | undefined, fallback = "-"): string {
  if (s === null || s === undefined || Number.isNaN(s)) return fallback;
  return String(Math.round(s / 60));
}

/** Human duration between two instants, e.g. "1m 05s", "2h 03m". */
export function fmtDuration(start: Input, end: Input, fallback = "-"): string {
  const a = toDate(start);
  const b = toDate(end);
  if (!a || !b) return fallback;
  return fmtMs(b.getTime() - a.getTime());
}

export function fmtMs(ms: number): string {
  if (ms < 0) ms = 0;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}
