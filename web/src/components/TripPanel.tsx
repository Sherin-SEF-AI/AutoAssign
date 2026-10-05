import { useEffect, useState, type ReactNode } from "react";
import type { Assignment, DriverLane } from "../state/queries";
import { fmtDateTime, fmtTime, secToMin } from "../lib/time";
import { Drawer } from "./Drawer";
import { ReasonChip } from "./UnassignedTray";
import { Badge, StatusChip, btnCls, btnPrimaryCls, fmtNum, inputCls } from "./ui";

function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-neutral-500">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

/** Trip detail with keyboard friendly lock, move and unassign actions. */
export function TripPanel({
  a,
  lane,
  lanes,
  onClose,
  onMove,
  onLock,
  busy,
  readOnly,
}: {
  a: Assignment | null;
  lane: DriverLane | null;
  lanes: DriverLane[];
  onClose: () => void;
  onMove: (tripId: string, toDriverId: string | null) => void;
  onLock: (tripId: string, lock: boolean) => void;
  busy: boolean;
  readOnly: boolean;
}) {
  const [target, setTarget] = useState("");
  useEffect(() => setTarget(""), [a?.trip_id]);
  if (!a) return null;
  const assigned = !!lane;
  const others = lanes.filter((l) => l.driver_id !== lane?.driver_id);
  return (
    <Drawer open title={`Trip ${fmtTime(a.scheduled_pickup_at)} ${a.trip_type ?? ""}`} onClose={onClose}>
      <div className="space-y-3">
        {!readOnly && (
          <div className="space-y-2 rounded border border-neutral-200 bg-neutral-50 p-2">
            <div className="flex flex-wrap items-center gap-1">
              <label htmlFor="move-target" className="text-neutral-600">
                {assigned ? "Move to" : "Assign to"}
              </label>
              <select id="move-target" className={`${inputCls} min-w-[200px]`} value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Choose driver...</option>
                {others.map((l) => (
                  <option key={l.driver_id} value={l.driver_id} disabled={!l.available}>
                    {l.name} {l.vehicle_registration ? `(${l.vehicle_registration})` : ""} {l.available ? "" : "unavailable"}
                  </option>
                ))}
              </select>
              <button type="button" className={btnPrimaryCls} disabled={!target || busy} onClick={() => onMove(a.trip_id, target)}>
                {assigned ? "Move" : "Assign"}
              </button>
            </div>
            {assigned && (
              <div className="flex flex-wrap gap-1">
                <button type="button" className={btnCls} disabled={busy} onClick={() => onLock(a.trip_id, !a.locked)}>
                  {a.locked ? "Unlock" : "Lock"}
                </button>
                <button type="button" className={btnCls} disabled={busy} onClick={() => onMove(a.trip_id, null)}>
                  Unassign
                </button>
              </div>
            )}
          </div>
        )}
        <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1">
          <KV k="Trip id"><span className="font-mono">{a.trip_id}</span></KV>
          <KV k="Driver">{lane ? `${lane.name} (${lane.vehicle_registration ?? "no vehicle"})` : "unassigned"}</KV>
          <KV k="Status"><StatusChip status={a.status} /></KV>
          <KV k="Type / channel">{a.trip_type ?? "-"} / {a.channel ?? "-"}</KV>
          <KV k="Vehicle class">{a.vehicle_class ?? "-"}</KV>
          <KV k="Flags">
            <span className="flex flex-wrap gap-0.5">
              {a.vip && <Badge tone="red">VIP</Badge>}
              {a.locked && <Badge>locked</Badge>}
              {a.account_id && <Badge tone="blue">acct {a.account_id}</Badge>}
              {a.unassigned_reason && <ReasonChip reason={a.unassigned_reason} />}
              {!lane && (a.acknowledged ? <Badge tone="green">acknowledged</Badge> : <Badge tone="amber">needs ack</Badge>)}
            </span>
          </KV>
          <KV k="Scheduled pickup">{fmtDateTime(a.scheduled_pickup_at)}</KV>
          <KV k="Pickup">{a.pickup_address ?? "-"}</KV>
          <KV k="Drop">{a.drop_address ?? "-"}</KV>
          <KV k="Seq">{a.seq ?? "-"}</KV>
          <KV k="Planned depart">{fmtTime(a.planned_depart_at)}</KV>
          <KV k="Arrive pickup">{fmtTime(a.planned_arrive_pickup_at)}</KV>
          <KV k="Planned pickup">{fmtTime(a.planned_pickup_at)}</KV>
          <KV k="Planned drop">{fmtTime(a.planned_drop_at)}</KV>
          <KV k="Deadhead">{secToMin(a.deadhead_s)} min, {a.deadhead_m != null ? `${fmtNum(a.deadhead_m / 1000, 1)} km` : "-"} ({a.deadhead_source ?? "-"})</KV>
          <KV k="Buffer">{secToMin(a.buffer_s)} min</KV>
          <KV k="Trip">{secToMin(a.trip_s)} min (p50 {secToMin(a.trip_p50_s)}), {a.trip_m != null ? `${fmtNum(a.trip_m / 1000, 1)} km` : "-"} ({a.trip_source ?? "-"})</KV>
          <KV k="Slack">{secToMin(a.slack_s)} min</KV>
          <KV k="Energy">{a.energy_wh != null ? `${fmtNum(a.energy_wh)} Wh` : "-"}</KV>
        </dl>
      </div>
    </Drawer>
  );
}
