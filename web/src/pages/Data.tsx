import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../state/auth";
import { useSearchParam, useServiceDate } from "../state/date";
import { JOB_TERMINAL, useEstimates, useJobRun, useRegenerate, useRunJob, useSnapshots, type Estimates } from "../state/queries";
import { ApiError } from "../api/errors";
import { fmtDateTime, isYmd } from "../lib/time";
import { ErrorBanner, toast } from "../components/Toast";
import { JobProgress } from "../components/JobProgress";
import { EmptyRow, JsonBlock, Placeholder, Section, Spinner, TableWrap, Td, Th, btnCls, btnPrimaryCls, fmtNum, inputCls } from "../components/ui";

export default function DataPage() {
  return (
    <div className="space-y-6">
      <RegenerateSection />
      <SnapshotsSection />
      <Section title="Replay runner">
        <Placeholder title="Replay runner" />
      </Section>
      <EstimatesSection />
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

const EST_KINDS = ["trip", "deadhead", "hub"];
const EST_SOURCES = ["here", "osrm", "fallback", "package"];
const GRAPH_KEYS = ["node_count", "vehicle_count", "candidate_edges", "edge_count", "start_legs", "end_legs", "buffer_policy"];
const ESTIMATOR_KEYS = ["here_routing_calls", "here_matrix_calls", "here_matrix_elements", "cache_hits", "skipped"];

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function StatList({ title, stats, keys }: { title: string; stats: Record<string, unknown>; keys: string[] }) {
  const extra = Object.keys(stats).filter((k) => !keys.includes(k));
  return (
    <div className="rounded border border-neutral-200 bg-white p-2">
      <div className="mb-1 font-semibold">{title}</div>
      <dl className="grid grid-cols-[160px_auto] gap-x-3 gap-y-0.5">
        {[...keys, ...extra].map((k) => {
          const v = stats[k];
          return (
            <div key={k} className="contents">
              <dt className="text-neutral-500">{k}</dt>
              <dd className="font-mono">{v === undefined || v === null ? "-" : typeof v === "number" ? fmtNum(v, Number.isInteger(v) ? 0 : 2) : typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

function EstimatesTable({ data }: { data: Estimates }) {
  const kinds = [...EST_KINDS, ...new Set(data.rows.map((r) => r.kind).filter((k) => !EST_KINDS.includes(k)))];
  const sources = [...EST_SOURCES, ...new Set(data.rows.map((r) => r.source).filter((x) => !EST_SOURCES.includes(x)))];
  const count = (k: string, src: string) => data.rows.filter((r) => r.kind === k && r.source === src).reduce((a, r) => a + r.count, 0);
  const rowTotal = (k: string) => data.rows.filter((r) => r.kind === k).reduce((a, r) => a + r.count, 0);
  const colTotal = (src: string) => data.rows.filter((r) => r.source === src).reduce((a, r) => a + r.count, 0);
  const grand = data.rows.reduce((a, r) => a + r.count, 0);
  return (
    <TableWrap className="max-w-2xl">
      <thead>
        <tr>
          <Th>Kind</Th>
          {sources.map((src) => (
            <Th key={src} className="text-right">
              {src}
            </Th>
          ))}
          <Th className="text-right">Total</Th>
        </tr>
      </thead>
      <tbody>
        {kinds.map((k) => (
          <tr key={k}>
            <Td className="font-medium">{k}</Td>
            {sources.map((src) => {
              const c = count(k, src);
              return (
                <Td key={src} className={`text-right font-mono ${c === 0 ? "text-neutral-300" : src === "fallback" ? "text-amber-700" : ""}`}>
                  {fmtNum(c)}
                </Td>
              );
            })}
            <Td className="text-right font-mono font-semibold">{fmtNum(rowTotal(k))}</Td>
          </tr>
        ))}
        <tr className="bg-neutral-50">
          <Td className="font-semibold">Total</Td>
          {sources.map((src) => (
            <Td key={src} className="text-right font-mono font-semibold">
              {fmtNum(colTotal(src))}
            </Td>
          ))}
          <Td className="text-right font-mono font-semibold">{fmtNum(grand)}</Td>
        </tr>
      </tbody>
    </TableWrap>
  );
}

function EstimatesSection() {
  const [date] = useServiceDate();
  const qc = useQueryClient();
  const q = useEstimates(date);
  const runJob = useRunJob();
  const [runId, setRunId] = useSearchParam("estrun");
  const run = useJobRun(runId);
  const status = run.data?.status;
  const running = !!runId && !(status && JOB_TERMINAL.has(status));

  useEffect(() => {
    if (status && JOB_TERMINAL.has(status)) {
      void qc.invalidateQueries({ queryKey: ["estimates"] });
      void qc.invalidateQueries({ queryKey: ["budget"] });
    }
  }, [status, qc]);

  const lastRun = q.data?.last_run ?? null;
  const stats = lastRun && isRecord(lastRun.stats) ? lastRun.stats : null;
  const graph = stats && isRecord(stats.graph) ? stats.graph : null;
  const estimator = stats && isRecord(stats.estimator) ? stats.estimator : null;

  return (
    <Section
      title={
        <>
          Estimates by source <span className="font-normal text-neutral-500">{date}</span>
        </>
      }
      right={
        <>
          {q.isFetching && <Spinner label="Refreshing" />}
          <button
            type="button"
            className={btnPrimaryCls}
            disabled={runJob.isPending || running}
            onClick={() => runJob.mutate({ name: "estimate_day", serviceDate: date }, { onSuccess: (out) => setRunId(out.run_id) })}
          >
            {runJob.isPending ? "Starting..." : running ? "Estimating..." : "Estimate this date"}
          </button>
        </>
      }
    >
      {runJob.error && <ErrorBanner error={runJob.error} />}
      {runId && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-xs text-neutral-500">
            <span>
              estimate_day run <span className="font-mono">{runId}</span>
            </span>
            <button type="button" className={btnCls} onClick={() => setRunId(null)}>
              Clear
            </button>
          </div>
          {run.error && <ErrorBanner error={run.error} onRetry={() => void run.refetch()} />}
          {run.data && <JobProgress run={run.data} />}
        </div>
      )}
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      {q.isLoading && <Spinner />}
      {q.data && (
        <div className="space-y-2 text-xs">
          <EstimatesTable data={q.data} />
          {lastRun ? (
            <div className="text-neutral-600">
              Last run: <span className="font-mono">{String(lastRun.job_name ?? "-")}</span> started{" "}
              {typeof lastRun.started_at === "string" ? fmtDateTime(lastRun.started_at) : "-"} IST
            </div>
          ) : (
            <div className="text-neutral-500">No estimate run recorded for this date.</div>
          )}
          {(graph || estimator) && (
            <div className="flex flex-wrap gap-3">
              {graph && <StatList title="Graph" stats={graph} keys={GRAPH_KEYS} />}
              {estimator && <StatList title="Estimator" stats={estimator} keys={ESTIMATOR_KEYS} />}
            </div>
          )}
          {stats && !graph && !estimator && <JsonBlock value={stats} label="last run stats" />}
        </div>
      )}
    </Section>
  );
}
