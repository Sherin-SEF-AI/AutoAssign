import { useState, type FormEvent } from "react";
import { useAuth } from "../state/auth";
import { useSearchParam, useServiceDate } from "../state/date";
import { JOB_TERMINAL, useJobRun, useRegenerate, useSnapshots } from "../state/queries";
import { ApiError } from "../api/errors";
import { fmtDateTime, isYmd } from "../lib/time";
import { ErrorBanner, toast } from "../components/Toast";
import { JobProgress } from "../components/JobProgress";
import { EmptyRow, JsonBlock, Placeholder, Section, Spinner, TableWrap, Td, Th, btnCls, btnPrimaryCls, inputCls } from "../components/ui";

export default function DataPage() {
  return (
    <div className="space-y-6">
      <RegenerateSection />
      <SnapshotsSection />
      <Section title="Replay runner">
        <Placeholder title="Replay runner" />
      </Section>
      <Section title="Estimates by source">
        <Placeholder title="Estimates by source" />
      </Section>
    </div>
  );
}

function NumField({ label, value, onChange, min, max, error, disabled }: { label: string; value: string; onChange: (v: string) => void; min: number; max: number; error?: string; disabled?: boolean }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-neutral-600">
        {label} <span className="text-neutral-400">({min} to {max})</span>
      </span>
      <input inputMode="numeric" className={`${inputCls} w-28 ${error ? "border-red-400" : ""}`} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
      {error && <span className="text-[11px] text-red-700">{error}</span>}
    </label>
  );
}

function RegenerateSection() {
  const { isAdmin } = useAuth();
  const regen = useRegenerate();
  const [runId, setRunId] = useSearchParam("run");
  const run = useJobRun(runId);
  const [seed, setSeed] = useState("42");
  const [daysPast, setDaysPast] = useState("45");
  const [daysFuture, setDaysFuture] = useState("7");
  const apiErr = regen.error instanceof ApiError ? regen.error : null;
  const running = !!runId && !(run.data && JOB_TERMINAL.has(run.data.status));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body = { seed: Number(seed), days_past: Number(daysPast), days_future: Number(daysFuture) };
    if (Object.values(body).some((v) => !Number.isInteger(v))) {
      toast.error(new ApiError("client_validation", "seed, days_past and days_future must be whole numbers", 0));
      return;
    }
    regen.mutate(body, {
      onSuccess: (out) => {
        toast.info("Regeneration started", `run ${out.run_id.slice(0, 8)}`);
        setRunId(out.run_id);
      },
    });
  };

  return (
    <Section title="Synthetic dataset">
      <form onSubmit={submit} noValidate className="flex flex-wrap items-end gap-3 rounded border border-neutral-200 bg-white p-3 text-xs">
        <NumField label="Seed" value={seed} onChange={setSeed} min={0} max={2147483648} error={apiErr?.fieldError("seed")} disabled={!isAdmin} />
        <NumField label="Days past" value={daysPast} onChange={setDaysPast} min={0} max={120} error={apiErr?.fieldError("days_past")} disabled={!isAdmin} />
        <NumField label="Days future" value={daysFuture} onChange={setDaysFuture} min={0} max={30} error={apiErr?.fieldError("days_future")} disabled={!isAdmin} />
        <button type="submit" className={btnPrimaryCls} disabled={!isAdmin || regen.isPending || running} title={isAdmin ? "" : "Admin role required"}>
          {regen.isPending ? "Submitting..." : running ? "Running..." : "Regenerate"}
        </button>
        {!isAdmin && <span className="text-neutral-500">Admin role required to regenerate.</span>}
        <span className="basis-full text-[11px] text-amber-700">Regenerating replaces all synthetic trips, drivers and vehicles in the window.</span>
      </form>
      {regen.error && <ErrorBanner error={regen.error} />}
      {runId && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-xs text-neutral-500">
            <span>
              Run <span className="font-mono">{runId}</span>
            </span>
            <button type="button" className={btnCls} onClick={() => setRunId(null)}>
              Clear
            </button>
          </div>
          {run.error && <ErrorBanner error={run.error} onRetry={() => void run.refetch()} />}
          {run.isLoading && <Spinner label="Waiting for job" />}
          {run.data && <JobProgress run={run.data} />}
        </div>
      )}
    </Section>
  );
}

function SnapshotsSection() {
  const [serviceDate] = useServiceDate();
  const [filter, setFilter] = useSearchParam("snapdate");
  const date = isYmd(filter) ? filter : null;
  const q = useSnapshots(date);
  const rows = q.data ?? [];
  return (
    <Section
      title={
        <>
          Snapshots <span className="font-normal text-neutral-500">({rows.length})</span>
        </>
      }
      right={
        <>
          <input type="date" aria-label="Snapshot date filter" className={inputCls} value={date ?? ""} onChange={(e) => setFilter(e.target.value || null)} />
          <button type="button" className={btnCls} onClick={() => setFilter(serviceDate)}>
            Use {serviceDate}
          </button>
          <button type="button" className={btnCls} onClick={() => setFilter(null)} disabled={!date}>
            All dates
          </button>
          {q.isFetching && <Spinner label="Refreshing" />}
        </>
      }
    >
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap className="max-h-[50vh]">
        <thead>
          <tr>
            <Th>Created (IST)</Th>
            <Th>Service date</Th>
            <Th>Source</Th>
            <Th>Reason</Th>
            <Th>Snapshot</Th>
            <Th>Parent</Th>
            <Th>Hash</Th>
            <Th>Counts</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={8}><Spinner /></EmptyRow>}
          {!q.isLoading && rows.length === 0 && <EmptyRow cols={8}>No snapshots</EmptyRow>}
          {rows.map((s) => (
            <tr key={s.snapshot_id}>
              <Td className="whitespace-nowrap font-mono">{fmtDateTime(s.created_at)}</Td>
              <Td className="whitespace-nowrap">{s.service_date}</Td>
              <Td>{s.source}</Td>
              <Td>{s.reason}</Td>
              <Td className="font-mono" title={s.snapshot_id}>
                {s.snapshot_id.slice(0, 8)}
              </Td>
              <Td className="font-mono" title={s.parent_snapshot_id ?? ""}>
                {s.parent_snapshot_id ? s.parent_snapshot_id.slice(0, 8) : "-"}
              </Td>
              <Td className="font-mono" title={s.content_hash}>
                {s.content_hash.slice(0, 12)}
              </Td>
              <Td>
                <CountsCell counts={s.counts as Record<string, unknown>} />
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </Section>
  );
}

function CountsCell({ counts }: { counts: Record<string, unknown> }) {
  const flat = Object.entries(counts).filter(([, v]) => typeof v === "number" || typeof v === "string");
  if (flat.length === Object.keys(counts).length && flat.length <= 6) {
    return <span className="whitespace-nowrap">{flat.map(([k, v]) => `${k} ${String(v)}`).join(", ") || "-"}</span>;
  }
  return <JsonBlock value={counts} label="counts" />;
}
