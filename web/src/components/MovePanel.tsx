import { useEffect, useState } from "react";
import type { DriverLane, Issue, MoveOut } from "../state/queries";
import { fmtTime, secToMin } from "../lib/time";
import { Drawer } from "./Drawer";
import { EmptyRow, TableWrap, Td, Th, btnPrimaryCls, fmtNum, inputCls } from "./ui";

function IssueList({ title, items, tone }: { title: string; items: Issue[]; tone: "red" | "amber" }) {
  if (items.length === 0) return null;
  const cls = tone === "red" ? "border-red-300 bg-red-50 text-red-900" : "border-amber-300 bg-amber-50 text-amber-900";
  return (
    <div className={`rounded border p-2 ${cls}`}>
      <div className="mb-1 font-semibold">
        {title} ({items.length})
      </div>
      <ul className="space-y-0.5">
        {items.map((e, i) => (
          <li key={i}>
            <span className="font-mono font-semibold">{e.code}</span>: {e.message}
            {e.trip_id && <span className="ml-1 font-mono text-[10px] opacity-70">trip {e.trip_id.slice(0, 8)}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Result of a move: errors, warnings, the resulting planned times, and a forced retry with a reason. */
export function MovePanel({
  result,
  lanesById,
  movedTripId,
  onForce,
  busy,
  onClose,
}: {
  result: MoveOut | null;
  lanesById: Map<string, DriverLane>;
  movedTripId: string | null;
  onForce: (reason: string) => void;
  busy: boolean;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  useEffect(() => setReason(""), [result]);
  if (!result) return null;
  return (
    <Drawer open title={result.accepted ? (result.forced ? "Move forced" : "Move applied with warnings") : "Move rejected"} onClose={onClose}>
      <div className="space-y-3">
        <div className={result.accepted ? "text-emerald-800" : "text-red-800"}>{result.message}</div>
        <IssueList title="Errors" items={result.errors} tone="red" />
        <IssueList title="Warnings" items={result.warnings} tone="amber" />
        {!result.accepted && (
          <form
            className="space-y-1 rounded border border-neutral-300 bg-neutral-50 p-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (reason.trim()) onForce(reason.trim());
            }}
          >
            <label className="block font-semibold" htmlFor="force-reason">
              Force this move
            </label>
            <div className="text-neutral-500">Forcing records an override. A reason is required.</div>
            <div className="flex gap-1">
              <input id="force-reason" className={`${inputCls} flex-1`} placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
              <button type="submit" className={btnPrimaryCls} disabled={!reason.trim() || busy}>
                {busy ? "..." : "Force move"}
              </button>
            </div>
          </form>
        )}
        {result.drivers.map((d) => {
          const lane = d.driver_id ? lanesById.get(d.driver_id) : undefined;
          const pct = d.energy_cap_wh > 0 ? (d.energy_wh / d.energy_cap_wh) * 100 : null;
          return (
            <div key={d.driver_id ?? "unassigned"}>
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <span className="font-semibold">{lane?.name ?? (d.driver_id ? d.driver_id.slice(0, 8) : "Unassigned")}</span>
                <span className="text-neutral-500">
                  energy {fmtNum(d.energy_wh)} / {fmtNum(d.energy_cap_wh)} Wh{pct !== null ? ` (${Math.round(pct)}%)` : ""}
                </span>
                <span className="text-neutral-500">ends {fmtTime(d.end_at)}</span>
              </div>
              <TableWrap>
                <thead>
                  <tr>
                    <Th>#</Th>
                    <Th>Trip</Th>
                    <Th>Depart</Th>
                    <Th>At pickup</Th>
                    <Th>Pickup</Th>
                    <Th>Drop</Th>
                    <Th className="text-right">Slack</Th>
                    <Th className="text-right">Deadhead</Th>
                    <Th className="text-right">Buffer</Th>
                  </tr>
                </thead>
                <tbody>
                  {d.stops.length === 0 && <EmptyRow cols={9}>No stops</EmptyRow>}
                  {d.stops.map((s) => (
                    <tr key={s.trip_id} className={s.trip_id === movedTripId ? "bg-sky-50 font-semibold" : ""}>
                      <Td>{s.seq}</Td>
                      <Td className="font-mono">{s.trip_id.slice(0, 8)}</Td>
                      <Td className="font-mono">{fmtTime(s.planned_depart_at)}</Td>
                      <Td className="font-mono">{fmtTime(s.planned_arrive_pickup_at)}</Td>
                      <Td className="font-mono">{fmtTime(s.planned_pickup_at)}</Td>
                      <Td className="font-mono">{fmtTime(s.planned_drop_at)}</Td>
                      <Td className={`text-right font-mono ${s.slack_s < 0 ? "text-red-700" : ""}`}>{secToMin(s.slack_s)}</Td>
                      <Td className="text-right font-mono">{secToMin(s.deadhead_s)}</Td>
                      <Td className="text-right font-mono">{secToMin(s.buffer_s)}</Td>
                    </tr>
                  ))}
                </tbody>
              </TableWrap>
            </div>
          );
        })}
        <div className="text-[11px] text-neutral-500">Times IST, durations in minutes.</div>
      </div>
    </Drawer>
  );
}
