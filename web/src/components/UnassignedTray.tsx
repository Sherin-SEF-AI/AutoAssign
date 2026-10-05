import { useState } from "react";
import type { Assignment } from "../state/queries";
import { startPointerDrag, suppressClick, useDragState } from "../state/drag";
import { fmtTime } from "../lib/time";
import { REASON_LABEL, REASON_TONE, tripColor } from "../lib/planStyle";
import { btnCls, inputCls } from "./ui";

export function ReasonChip({ reason }: { reason: string | null | undefined }) {
  if (!reason) return null;
  return <span className={`whitespace-nowrap rounded border px-1 text-[10px] ${REASON_TONE[reason] ?? "border-neutral-200 bg-neutral-50 text-neutral-700"}`}>{REASON_LABEL[reason] ?? reason}</span>;
}

/** Unassigned trips. Drag a card onto a lane to assign it; drop a timeline block here to unassign. */
export function UnassignedTray({
  items,
  onOpen,
  onMove,
  onAcknowledgeAll,
  ackBusy,
  readOnly,
}: {
  items: Assignment[];
  onOpen: (a: Assignment) => void;
  onMove: (tripId: string, toDriverId: string | null) => void;
  onAcknowledgeAll: (note: string) => void;
  ackBusy: boolean;
  readOnly: boolean;
}) {
  const drag = useDragState();
  const [note, setNote] = useState("");
  const over = !!drag && drag.over === null && drag.fromDriverId !== null;
  const pending = items.filter((a) => !a.acknowledged).length;
  const sorted = [...items].sort((a, b) => (a.scheduled_pickup_at ?? "").localeCompare(b.scheduled_pickup_at ?? ""));
  return (
    <div data-drop-driver="" className={`rounded border bg-white p-2 text-xs ${over ? "border-sky-400 bg-sky-50" : "border-neutral-200"}`}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="font-semibold">Unassigned ({items.length})</span>
        <span className="text-neutral-500">{pending} not acknowledged</span>
        <span className="text-[11px] text-neutral-400">Drag a card onto a driver lane to assign; drop a trip here to unassign.</span>
        {!readOnly && (
          <span className="ml-auto flex items-center gap-1">
            <input className={`${inputCls} w-48`} placeholder="Acknowledgement note" value={note} onChange={(e) => setNote(e.target.value)} />
            <button type="button" className={btnCls} disabled={ackBusy || pending === 0} onClick={() => onAcknowledgeAll(note.trim())}>
              {ackBusy ? "..." : "Acknowledge all"}
            </button>
          </span>
        )}
      </div>
      {items.length === 0 ? (
        <div className="py-2 text-center text-neutral-400">All trips assigned</div>
      ) : (
        <div className="flex max-h-48 flex-wrap gap-1.5 overflow-y-auto">
          {sorted.map((a) => (
            <button
              key={a.trip_id}
              type="button"
              className="flex w-56 cursor-grab select-none flex-col gap-0.5 rounded border border-neutral-200 bg-neutral-50 p-1.5 text-left hover:border-neutral-400"
              onPointerDown={(e) =>
                !readOnly && startPointerDrag(e, { tripId: a.trip_id, fromDriverId: null, label: `${fmtTime(a.scheduled_pickup_at)} ${a.trip_type ?? ""}` }, onMove)
              }
              onClick={() => !suppressClick() && onOpen(a)}
              title={`${a.pickup_address ?? ""} to ${a.drop_address ?? ""}`}
            >
              <span className="flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-sm" style={{ background: tripColor(a.trip_type) }} />
                <span className="font-mono font-semibold">{fmtTime(a.scheduled_pickup_at)}</span>
                <span>{a.trip_type}</span>
                {a.vehicle_class && <span className="text-[10px] text-neutral-500">{a.vehicle_class}</span>}
                {a.vip && <span className="rounded bg-red-100 px-0.5 text-[9px] text-red-800">VIP</span>}
                <span className="ml-auto">{a.acknowledged ? <span className="text-[10px] text-emerald-700">ack</span> : <span className="text-[10px] text-amber-700">needs ack</span>}</span>
              </span>
              <span className="truncate text-[11px] text-neutral-600">{a.pickup_address ?? "-"}</span>
              <span className="flex flex-wrap gap-0.5">
                <ReasonChip reason={a.unassigned_reason} />
                {a.account_id && <span className="text-[10px] text-neutral-500">acct {a.account_id}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
