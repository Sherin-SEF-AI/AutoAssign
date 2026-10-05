import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../state/auth";
import { useFactors, useSaveSettings, useSettings, useSettingsAudit, type Factor, type Factors } from "../state/queries";
import { ApiError } from "../api/errors";
import { fmtDateTime } from "../lib/time";
import { ErrorBanner, toast } from "../components/Toast";
import { Badge, EmptyRow, Section, Spinner, TableWrap, Td, Th, btnCls, btnPrimaryCls, fmtNum, inputCls } from "../components/ui";

const GROUP_ORDER = ["buffers", "caps", "thresholds", "providers"];
const MODELS = ["tigor_ev", "ec3", "windsor", "zs_ev"];
const MODEL_MAP_KEYS = new Set(["range_caps_json", "e_roll_json"]);

type Draft = string | boolean | Record<string, string>;
type Value = unknown;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function toDraft(v: Value): Draft {
  if (typeof v === "boolean") return v;
  if (isRecord(v)) {
    const out: Record<string, string> = {};
    for (const k of new Set([...MODELS, ...Object.keys(v)])) out[k] = v[k] === undefined || v[k] === null ? "" : String(v[k]);
    return out;
  }
  return v === null || v === undefined ? "" : String(v);
}

/** Parse a draft back into an API value. Returns an error string on bad input. */
function fromDraft(d: Draft, original: Value): { value: unknown } | { error: string } {
  if (typeof d === "boolean") return { value: d };
  if (typeof d === "string") {
    if (typeof original !== "number") return { value: d };
    if (d.trim() === "") return { error: "required" };
    const n = Number(d);
    return Number.isFinite(n) ? { value: n } : { error: "not a number" };
  }
  const out: Record<string, number> = {};
  for (const [k, s] of Object.entries(d)) {
    if (s.trim() === "") {
      if (isRecord(original) && k in original) return { error: `${k}: required` };
      continue; // model not configured and left blank
    }
    const n = Number(s);
    if (!Number.isFinite(n)) return { error: `${k}: not a number` };
    out[k] = n;
  }
  return { value: out };
}

function stable(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(stable);
  if (isRecord(v)) return Object.fromEntries(Object.keys(v).sort().map((k) => [k, stable(v[k])]));
  return v;
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(stable(a)) === JSON.stringify(stable(b));
}

function unitHint(key: string): string {
  if (key.endsWith("_s")) return "s";
  if (key.endsWith("_km")) return "km";
  if (key.endsWith("_pct")) return "%";
  if (key === "range_caps_json") return "km per model";
  if (key === "e_roll_json") return "Wh/km per model";
  return "";
}

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <SettingsForm />
      <AuditList />
      <FactorTable />
    </div>
  );
}

function SettingsForm() {
  const { isAdmin } = useAuth();
  const q = useSettings();
  const save = useSaveSettings();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const values = (q.data?.values ?? {}) as Record<string, Value>;
  const groups = q.data?.groups ?? {};

  // (Re)initialise drafts whenever the server values change.
  useEffect(() => {
    if (!q.data) return;
    const next: Record<string, Draft> = {};
    for (const [k, v] of Object.entries(q.data.values as Record<string, Value>)) next[k] = toDraft(v);
    setDrafts(next);
  }, [q.data]);

  const parsed = useMemo(() => {
    const changes: Record<string, unknown> = {};
    const errors: Record<string, string> = {};
    for (const [k, d] of Object.entries(drafts)) {
      const r = fromDraft(d, values[k]);
      if ("error" in r) errors[k] = r.error;
      else if (!sameValue(r.value, values[k])) changes[k] = r.value;
    }
    return { changes, errors };
  }, [drafts, values]);

  const grouped = useMemo(() => {
    const keys = Object.keys(values);
    const names = [...GROUP_ORDER, ...new Set(keys.map((k) => groups[k] ?? "other").filter((g) => !GROUP_ORDER.includes(g)))];
    return names.map((g) => ({ name: g, keys: keys.filter((k) => (groups[k] ?? "other") === g) })).filter((g) => g.keys.length > 0);
  }, [values, groups]);

  const apiErr = save.error instanceof ApiError ? save.error : null;
  const errFor = (key: string): string[] => {
    if (!apiErr) return [];
    return apiErr.errors.filter((e) => e.field === key || e.field.startsWith(`${key}.`)).map((e) => (e.field === key ? e.message : `${e.field.slice(key.length + 1)}: ${e.message}`));
  };
  const unmatched = apiErr ? apiErr.errors.filter((e) => !Object.keys(values).some((k) => e.field === k || e.field.startsWith(`${k}.`))) : [];

  const nChanged = Object.keys(parsed.changes).length;
  const nInvalid = Object.keys(parsed.errors).length;

  const onSave = () => {
    if (nInvalid > 0) {
      toast.error(new ApiError("client_validation", `fix ${nInvalid} invalid field(s) before saving`, 0));
      return;
    }
    save.mutate(parsed.changes, { onSuccess: () => toast.success("Settings saved", Object.keys(parsed.changes).join(", ")) });
  };

  const reset = () => {
    const next: Record<string, Draft> = {};
    for (const [k, v] of Object.entries(values)) next[k] = toDraft(v);
    setDrafts(next);
    save.reset();
  };

  const setDraft = (k: string, d: Draft) => setDrafts((prev) => ({ ...prev, [k]: d }));

  return (
    <Section
      title="Settings"
      right={
        isAdmin ? (
          <>
            <span className="text-xs text-neutral-500">
              {nChanged} changed{nInvalid ? `, ${nInvalid} invalid` : ""}
            </span>
            <button type="button" className={btnCls} onClick={reset} disabled={nChanged === 0 && !save.error}>
              Reset
            </button>
            <button type="button" className={btnPrimaryCls} onClick={onSave} disabled={save.isPending || nChanged === 0}>
              {save.isPending ? "Saving..." : "Save changes"}
            </button>
          </>
        ) : (
          <span className="text-xs text-neutral-500">Read-only (admin role required to edit)</span>
        )
      }
    >
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading && <Spinner />}
      {save.error && (
        <ErrorBanner
          error={unmatched.length || !apiErr ? save.error : new ApiError(apiErr.code, `${apiErr.detail} (see highlighted fields)`, apiErr.status)}
        />
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {grouped.map((g) => (
          <fieldset key={g.name} className="rounded border border-neutral-200 bg-white p-2 text-xs">
            <legend className="px-1 text-[11px] font-semibold uppercase tracking-wide text-neutral-600">{g.name}</legend>
            <div className="divide-y divide-neutral-100">
              {g.keys.map((k) => {
                const d = drafts[k];
                if (d === undefined) return null;
                const changed = k in parsed.changes;
                const errs = [...(parsed.errors[k] ? [parsed.errors[k]] : []), ...errFor(k)];
                return (
                  <div key={k} className={`flex flex-wrap items-start gap-2 py-1 ${changed ? "bg-amber-50" : ""}`}>
                    <label className="w-56 shrink-0 pt-0.5 font-mono text-[11px]" htmlFor={`set-${k}`}>
                      {k}
                      {unitHint(k) && <span className="ml-1 font-sans text-neutral-400">({unitHint(k)})</span>}
                    </label>
                    <div className="min-w-0 flex-1">
                      <SettingInput id={`set-${k}`} name={k} draft={d} disabled={!isAdmin} onChange={(nd) => setDraft(k, nd)} hasError={errs.length > 0} />
                      {errs.map((e, i) => (
                        <div key={i} className="text-[11px] text-red-700">
                          {e}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
    </Section>
  );
}

function SettingInput({ id, name, draft, disabled, onChange, hasError }: { id: string; name: string; draft: Draft; disabled: boolean; onChange: (d: Draft) => void; hasError: boolean }) {
  if (typeof draft === "boolean") {
    return (
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={draft}
        disabled={disabled}
        onClick={() => onChange(!draft)}
        className={`relative inline-flex h-4 w-8 items-center rounded-full transition-colors disabled:opacity-60 ${draft ? "bg-emerald-500" : "bg-neutral-300"}`}
      >
        <span className={`inline-block h-3 w-3 rounded-full bg-white shadow transition-transform ${draft ? "translate-x-4" : "translate-x-0.5"}`} />
        <span className="sr-only">{draft ? "on" : "off"}</span>
      </button>
    );
  }
  if (typeof draft === "string") {
    return <input id={id} inputMode="decimal" disabled={disabled} className={`${inputCls} w-32 ${hasError ? "border-red-400" : ""}`} value={draft} onChange={(e) => onChange(e.target.value)} />;
  }
  const isModelMap = MODEL_MAP_KEYS.has(name);
  return (
    <div className="flex flex-wrap gap-2">
      {Object.entries(draft).map(([k, v]) => (
        <label key={k} className="flex items-center gap-1">
          <span className="font-mono text-[11px] text-neutral-500">{k}</span>
          <input
            inputMode="decimal"
            disabled={disabled}
            aria-label={`${name} ${k}`}
            className={`${inputCls} w-16 ${hasError ? "border-red-400" : ""} ${isModelMap && v === "" ? "border-dashed" : ""}`}
            value={v}
            onChange={(e) => onChange({ ...draft, [k]: e.target.value })}
          />
        </label>
      ))}
    </div>
  );
}

function AuditList() {
  const q = useSettingsAudit();
  const rows = q.data ?? [];
  return (
    <Section title={<>Settings audit <span className="font-normal text-neutral-500">(latest {rows.length})</span></>} right={q.isFetching && <Spinner label="Refreshing" />}>
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap className="max-h-[40vh]">
        <thead>
          <tr>
            <Th>When (IST)</Th>
            <Th>Actor</Th>
            <Th>Changes</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={3}><Spinner /></EmptyRow>}
          {!q.isLoading && rows.length === 0 && <EmptyRow cols={3}>No changes recorded</EmptyRow>}
          {rows.map((r, i) => (
            <tr key={i}>
              <Td className="whitespace-nowrap font-mono">{fmtDateTime(r.created_at)}</Td>
              <Td className="whitespace-nowrap">{r.actor}</Td>
              <Td>
                <ul className="space-y-0.5">
                  {Object.entries(r.changes as Record<string, unknown>).map(([k, v]) => (
                    <li key={k} className="break-all font-mono text-[11px]">
                      <span className="text-neutral-500">{k}</span>: {typeof v === "object" ? JSON.stringify(v) : String(v)}
                    </li>
                  ))}
                </ul>
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </Section>
  );
}

function hhmm(min: number): string {
  const m = ((min % 1440) + 1440) % 1440;
  return min >= 1440 ? "24:00" : `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** Labels for time bins. time_bin is an index into the speed profile; start_min is minutes after midnight IST. */
function binLabels(f: Factors): Map<number, string> {
  const bins = f.time_bins.map((b) => ({
    start: typeof b.start_min === "number" ? b.start_min : NaN,
    kmh: typeof b.kmh === "number" ? b.kmh : NaN,
  }));
  const out = new Map<number, string>();
  bins.forEach((b, i) => {
    if (!Number.isFinite(b.start)) return;
    const later = bins.map((x) => x.start).filter((x) => Number.isFinite(x) && x > b.start);
    const end = later.length ? Math.min(...later) : 1440;
    out.set(i, `${hhmm(b.start)}-${hhmm(end)}${Number.isFinite(b.kmh) ? ` ${fmtNum(b.kmh, 0)} km/h` : ""}`);
  });
  return out;
}

function FactorTable() {
  const q = useFactors();
  const [basis, setBasis] = useState("");
  const [level, setLevel] = useState("");
  const [showDefaults, setShowDefaults] = useState(false);
  const data = q.data;
  const all: Factor[] = useMemo(() => (data ? [...data.rows, ...(showDefaults ? data.defaults : [])] : []), [data, showDefaults]);
  const bases = useMemo(() => [...new Set(all.map((r) => r.basis))].sort(), [all]);
  const levels = useMemo(() => [...new Set(all.map((r) => r.level))].sort(), [all]);
  const labels = useMemo(() => (data ? binLabels(data) : new Map<number, string>()), [data]);
  const clusters = useMemo(() => new Map((data?.clusters ?? []).map((c) => [c.cluster_id, c])), [data]);
  const rows = all.filter((r) => (!basis || r.basis === basis) && (!level || r.level === level));

  return (
    <Section
      title="Factor table"
      right={
        <>
          {data && (
            <span className="flex items-center gap-1 text-xs">
              {data.calibrated ? <Badge tone="green">calibrated</Badge> : <Badge tone="amber">not calibrated</Badge>}
              <Badge>buffer: {data.buffer_policy}</Badge>
              {data.calibration_id && <span className="font-mono text-[11px] text-neutral-500">{data.calibration_id}</span>}
            </span>
          )}
          <select aria-label="Basis filter" className={inputCls} value={basis} onChange={(e) => setBasis(e.target.value)}>
            <option value="">All bases</option>
            {bases.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
          <select aria-label="Level filter" className={inputCls} value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">All levels</option>
            {levels.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-xs">
            <input type="checkbox" checked={showDefaults} onChange={(e) => setShowDefaults(e.target.checked)} />
            include defaults
          </label>
          <span className="text-xs text-neutral-500">{rows.length} rows</span>
        </>
      }
    >
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap className="max-h-[60vh]">
        <thead>
          <tr>
            <Th>Basis</Th>
            <Th>Level</Th>
            <Th>Time bin (IST)</Th>
            <Th>Weekday</Th>
            <Th>Zone cluster</Th>
            <Th className="text-right">r50</Th>
            <Th className="text-right">r80</Th>
            <Th className="text-right">r90</Th>
            <Th className="text-right">n</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={9}><Spinner /></EmptyRow>}
          {!q.isLoading && rows.length === 0 && <EmptyRow cols={9}>No factor rows{data && !data.calibrated && !showDefaults ? " (not calibrated: tick include defaults)" : ""}</EmptyRow>}
          {rows.map((r, i) => {
            const c = r.zone_cluster != null ? clusters.get(r.zone_cluster) : undefined;
            return (
              <tr key={i} className={r.level === "default" ? "text-neutral-500" : ""}>
                <Td>{r.basis}</Td>
                <Td>{r.level}</Td>
                <Td className="whitespace-nowrap font-mono">{r.time_bin == null ? "any" : (labels.get(r.time_bin) ?? `bin ${r.time_bin}`)}</Td>
                <Td>{r.weekday_type ?? "any"}</Td>
                <Td className="font-mono" title={c ? `${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}` : ""}>
                  {r.zone_cluster ?? "any"}
                </Td>
                <Td className="text-right font-mono">{r.r50.toFixed(3)}</Td>
                <Td className="text-right font-mono">{r.r80.toFixed(3)}</Td>
                <Td className="text-right font-mono">{r.r90.toFixed(3)}</Td>
                <Td className="text-right font-mono">{fmtNum(r.n)}</Td>
              </tr>
            );
          })}
        </tbody>
      </TableWrap>
    </Section>
  );
}
