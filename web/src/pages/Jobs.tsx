import { useMemo, useState } from "react";
import { useSearchParam } from "../state/date";
import { useJobRuns, useJobs, useRunJob, type JobInfo } from "../state/queries";
import { fmtDateTime, fmtDuration, todayIST, tomorrowIST } from "../lib/time";
import { ErrorBanner } from "../components/Toast";
import { EmptyRow, JsonBlock, Section, Spinner, StatusChip, TableWrap, Td, Th, btnCls, btnPrimaryCls, inputCls } from "../components/ui";

export default function JobsPage() {
  return (
    <div className="space-y-6">
      <JobList />
      <RunsTable />
    </div>
  );
}

function defaultDateFor(job: JobInfo): string {
  if (job.default_date === "tomorrow") return tomorrowIST();
  if (job.default_date === "today") return todayIST();
  return "";
}

function JobRow({ job }: { job: JobInfo }) {
  const run = useRunJob();
  const [date, setDate] = useState(() => defaultDateFor(job));
  return (
    <tr>
      <Td className="whitespace-nowrap font-mono">{job.name}</Td>
      <Td>{job.description}</Td>
      <Td className="whitespace-nowrap text-neutral-500">{job.default_date}</Td>
      <Td className="whitespace-nowrap">
        <div className="flex items-center gap-1">
          <input type="date" aria-label={`Service date for ${job.name}`} className={inputCls} value={date} onChange={(e) => setDate(e.target.value)} />
          <button type="button" className={btnPrimaryCls} disabled={run.isPending} onClick={() => run.mutate({ name: job.name, serviceDate: date || null })}>
            {run.isPending ? "Starting..." : "Run"}
          </button>
        </div>
        {run.error && <ErrorBanner error={run.error} className="mt-1" />}
      </Td>
    </tr>
  );
}

function JobList() {
  const q = useJobs();
  const jobs = q.data ?? [];
  return (
    <Section title="Jobs">
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap>
        <thead>
          <tr>
            <Th>Name</Th>
            <Th>Description</Th>
            <Th>Default date</Th>
            <Th>Run (service date optional)</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={4}><Spinner /></EmptyRow>}
          {!q.isLoading && jobs.length === 0 && <EmptyRow cols={4}>No jobs registered</EmptyRow>}
          {jobs.map((j) => (
            <JobRow key={j.name} job={j} />
          ))}
        </tbody>
      </TableWrap>
    </Section>
  );
}

function RunsTable() {
  const jobs = useJobs();
  const [name, setName] = useSearchParam("job");
  const q = useJobRuns(name ?? "");
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const total = q.data?.pages[0]?.total;
  return (
    <Section
      title={
        <>
          Runs{" "}
          <span className="font-normal text-neutral-500">
            ({rows.length}
            {typeof total === "number" ? ` of ${total}` : ""}, refreshes every 5 s)
          </span>
        </>
      }
      right={
        <>
          <select aria-label="Filter by job" className={inputCls} value={name ?? ""} onChange={(e) => setName(e.target.value || null)}>
            <option value="">All jobs</option>
            {(jobs.data ?? []).map((j) => (
              <option key={j.name} value={j.name}>
                {j.name}
              </option>
            ))}
          </select>
          {q.isFetching && <Spinner label="Refreshing" />}
        </>
      }
    >
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap className="max-h-[65vh]">
        <thead>
          <tr>
            <Th>Job</Th>
            <Th>Service date</Th>
            <Th>Status</Th>
            <Th>Started (IST)</Th>
            <Th>Duration</Th>
            <Th>Stats</Th>
            <Th>Error</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={7}><Spinner /></EmptyRow>}
          {!q.isLoading && rows.length === 0 && <EmptyRow cols={7}>No runs</EmptyRow>}
          {rows.map((r) => (
            <tr key={r.id}>
              <Td className="whitespace-nowrap font-mono" title={r.id}>
                {r.job_name}
              </Td>
              <Td className="whitespace-nowrap">{r.service_date ?? "-"}</Td>
              <Td>
                <StatusChip status={r.status} />
              </Td>
              <Td className="whitespace-nowrap font-mono">{fmtDateTime(r.started_at)}</Td>
              <Td className="whitespace-nowrap font-mono">{r.finished_at ? fmtDuration(r.started_at, r.finished_at) : <span className="text-sky-700">{fmtDuration(r.started_at, new Date())}+</span>}</Td>
              <Td>
                <JsonBlock value={r.stats} label="stats" />
              </Td>
              <Td className="max-w-[360px] break-words font-mono text-red-700">{r.error ?? ""}</Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
      {q.hasNextPage && (
        <div className="flex justify-center">
          <button type="button" className={btnCls} disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
            {q.isFetchingNextPage ? "Loading..." : "Load more"}
          </button>
        </div>
      )}
    </Section>
  );
}
