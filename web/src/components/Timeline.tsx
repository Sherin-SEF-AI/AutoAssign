import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import type { Assignment, DriverLane } from "../state/queries";
import { startPointerDrag, suppressClick, useDragState } from "../state/drag";
import { fmtTime, secToMin } from "../lib/time";
import { tripColor } from "../lib/planStyle";

/* Custom SVG timeline: 05:00 to 24:00 IST, one virtualised lane per driver. */

const LABEL_W = 236;
const LANE_H = 46;
const AXIS_H = 22;
const OVERSCAN = 4;
const MIN_PLOT_W = 720;
const START_HOUR = 5;
const END_HOUR = 24;

export interface LaneDiff {
  gained: number;
  lost: number;
}

export interface TimelineProps {
  date: string;
  lanes: DriverLane[];
  selectedDriverId: string | null;
  onSelectDriver: (driverId: string | null) => void;
  selectedTripId: string | null;
  onOpenTrip: (a: Assignment, lane: DriverLane | null) => void;
  onMove: (tripId: string, toDriverId: string | null) => void;
  changedTrips: Set<string>;
  driverDiff: Map<string, LaneDiff>;
  colorFor: (driverId: string) => string;
  busy?: boolean;
  maxHeight?: number | string;
  /** Draw a vertical "now" line at this instant (ms). */
  nowMs?: number | null;
  /** Colour trip blocks by live status (completed greyed, started highlighted). */
  liveStatus?: boolean;
  /** Extra per-lane content shown in a column right of the label (risk, actions). */
  laneExtra?: (lane: DriverLane) => ReactNode;
  laneExtraWidth?: number;
}

interface Hover {
  a: Assignment;
  lane: DriverLane;
  x: number;
  y: number;
}

interface Pick {
  tripId: string;
  fromIndex: number;
  target: number;
}

function ms(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

export function Timeline(p: TimelineProps) {
  const { date, lanes } = p;
  const wrapRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(600);
  const [hover, setHover] = useState<Hover | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const drag = useDragState();

  useLayoutEffect(() => {
    const el = wrapRef.current;
    const body = bodyRef.current;
    if (!el || !body) return;
    const ro = new ResizeObserver(() => {
      setWidth(el.clientWidth);
      setViewH(body.clientHeight);
    });
    ro.observe(el);
    ro.observe(body);
    return () => ro.disconnect();
  }, []);

  const t0 = useMemo(() => Date.parse(`${date}T00:00:00+05:30`), [date]);
  const start = t0 + START_HOUR * 3600_000;
  const end = t0 + END_HOUR * 3600_000;
  const extraW = p.laneExtra ? (p.laneExtraWidth ?? 170) : 0;
  const labelW = LABEL_W + extraW;
  const plotW = Math.max(MIN_PLOT_W, width - labelW - 2);
  const x = useCallback((t: number) => ((Math.min(Math.max(t, start), end) - start) / (end - start)) * plotW, [start, end, plotW]);
  const hours = useMemo(() => Array.from({ length: END_HOUR - START_HOUR + 1 }, (_, i) => START_HOUR + i), []);
  const nowX = p.nowMs != null && p.nowMs >= start && p.nowMs <= end ? x(p.nowMs) : null;

  const total = lanes.length * LANE_H;
  const first = Math.max(0, Math.floor(scrollTop / LANE_H) - OVERSCAN);
  const last = Math.min(lanes.length, Math.ceil((scrollTop + viewH) / LANE_H) + OVERSCAN);

  const scrollLaneIntoView = (i: number) => {
    const body = bodyRef.current;
    if (!body) return;
    const top = i * LANE_H;
    if (top < body.scrollTop) body.scrollTop = top;
    else if (top + LANE_H > body.scrollTop + body.clientHeight) body.scrollTop = top + LANE_H - body.clientHeight;
  };

  const onBodyKey = (e: KeyboardEvent) => {
    if (!pick) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = Math.max(0, Math.min(lanes.length - 1, pick.target + (e.key === "ArrowDown" ? 1 : -1)));
      setPick({ ...pick, target: next });
      scrollLaneIntoView(next);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const to = lanes[pick.target];
      setPick(null);
      if (to && pick.target !== pick.fromIndex) p.onMove(pick.tripId, to.driver_id);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setPick(null);
    }
  };

  const onBlockKey = (e: KeyboardEvent, a: Assignment, lane: DriverLane, laneIndex: number) => {
    if (pick) return; // handled by the body
    if (e.key === "Enter") {
      e.preventDefault();
      p.onOpenTrip(a, lane);
    } else if (e.key === " ") {
      e.preventDefault();
      e.stopPropagation();
      setPick({ tripId: a.trip_id, fromIndex: laneIndex, target: laneIndex });
      bodyRef.current?.focus();
    }
  };

  useEffect(() => {
    if (pick && !lanes.some((l) => l.assignments.some((a) => a.trip_id === pick.tripId))) setPick(null);
  }, [lanes, pick]);

  const pickTarget = pick ? lanes[pick.target] : undefined;

  return (
    <div ref={wrapRef} className="relative select-none overflow-x-auto rounded border border-neutral-200 bg-white text-xs">
      <svg width={0} height={0} className="absolute" aria-hidden>
        <defs>
          <pattern id="tl-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="#fef3c7" />
            <line x1="0" y1="0" x2="0" y2="5" stroke="#d97706" strokeWidth="1.6" />
          </pattern>
        </defs>
      </svg>

      <div style={{ width: labelW + plotW }}>
        {/* axis */}
        <div className="sticky top-0 z-[2] flex border-b border-neutral-200 bg-neutral-50" style={{ height: AXIS_H }}>
          <div className="shrink-0 px-2 py-1 text-[11px] text-neutral-500" style={{ width: labelW }}>
            {lanes.length} drivers (IST)
          </div>
          <svg width={plotW} height={AXIS_H} className="shrink-0">
            {hours.map((h) => {
              const hx = x(t0 + h * 3600_000);
              return (
                <g key={h}>
                  <line x1={hx} x2={hx} y1={AXIS_H - 5} y2={AXIS_H} stroke="#a3a3a3" />
                  {h < END_HOUR && (
                    <text x={hx + 2} y={13} fontSize={10} fill="#525252">
                      {String(h).padStart(2, "0")}
                    </text>
                  )}
                </g>
              );
            })}
            {nowX !== null && (
              <g>
                <line x1={nowX} x2={nowX} y1={0} y2={AXIS_H} stroke="#dc2626" strokeWidth={2} />
                <text x={nowX + 3} y={10} fontSize={9} fill="#dc2626">
                  now {fmtTime(p.nowMs)}
                </text>
              </g>
            )}
          </svg>
        </div>

        {/* lanes */}
        <div
          ref={bodyRef}
          tabIndex={-1}
          onKeyDown={onBodyKey}
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          className="relative overflow-y-auto overflow-x-hidden outline-none"
          style={{ maxHeight: p.maxHeight ?? "62vh", height: Math.max(LANE_H * 3, Math.min(total, 2000)) }}
        >
          <div className="relative" style={{ height: total }}>
            {lanes.slice(first, last).map((lane, k) => {
              const i = first + k;
              const isDragOver = !!drag && drag.over === lane.driver_id && drag.fromDriverId !== lane.driver_id;
              const isPickTarget = !!pick && pick.target === i && pick.fromIndex !== i;
              return (
                <LaneRow
                  key={lane.driver_id}
                  top={i * LANE_H}
                  lane={lane}
                  index={i}
                  plotW={plotW}
                  x={x}
                  t0={t0}
                  hours={hours}
                  selected={p.selectedDriverId === lane.driver_id}
                  highlight={isDragOver || isPickTarget}
                  color={p.colorFor(lane.driver_id)}
                  diff={p.driverDiff.get(lane.driver_id)}
                  extra={p.laneExtra ? p.laneExtra(lane) : null}
                  extraW={extraW}
                  nowX={nowX}
                  liveStatus={!!p.liveStatus}
                  changedTrips={p.changedTrips}
                  selectedTripId={p.selectedTripId}
                  pickedTripId={pick?.tripId ?? null}
                  onSelect={() => p.onSelectDriver(p.selectedDriverId === lane.driver_id ? null : lane.driver_id)}
                  onHover={(a, ev) => setHover(a && ev ? { a, lane, x: ev.clientX, y: ev.clientY } : null)}
                  onOpen={(a) => !suppressClick() && p.onOpenTrip(a, lane)}
                  onBlockKey={(e, a) => onBlockKey(e, a, lane, i)}
                  onDragStart={(e, a) =>
                    startPointerDrag(e, { tripId: a.trip_id, fromDriverId: lane.driver_id, label: `${fmtTime(a.planned_pickup_at)} ${a.trip_type ?? ""}` }, p.onMove)
                  }
                />
              );
            })}
          </div>
        </div>
      </div>

      {lanes.length === 0 && <div className="p-4 text-center text-neutral-500">No driver lanes in this plan</div>}

      {pick && (
        <div role="status" aria-live="polite" className="absolute bottom-2 left-2 z-[3] rounded bg-neutral-900 px-2 py-1 text-[11px] text-white shadow">
          Moving trip: target {pickTarget?.name ?? "-"} (arrows to choose, Enter to drop, Esc to cancel)
        </div>
      )}
      {p.busy && <div className="absolute inset-0 z-[4] flex items-center justify-center bg-white/50 text-xs font-medium">Working...</div>}
      {hover && !drag && <Tooltip h={hover} />}
      {drag && (
        <div className="pointer-events-none fixed z-[90] rounded border border-neutral-800 bg-white px-1.5 py-0.5 text-[11px] shadow" style={{ left: drag.x + 12, top: drag.y + 8 }}>
          {drag.label}
          <span className="ml-1 text-neutral-500">{drag.over === undefined ? "" : drag.over === null ? "to unassigned" : `to ${lanes.find((l) => l.driver_id === drag.over)?.name ?? "driver"}`}</span>
        </div>
      )}
    </div>
  );
}

interface LaneRowProps {
  top: number;
  lane: DriverLane;
  index: number;
  plotW: number;
  x: (t: number) => number;
  t0: number;
  hours: number[];
  selected: boolean;
  highlight: boolean;
  color: string;
  diff: LaneDiff | undefined;
  extra: ReactNode;
  extraW: number;
  nowX: number | null;
  liveStatus: boolean;
  changedTrips: Set<string>;
  selectedTripId: string | null;
  pickedTripId: string | null;
  onSelect: () => void;
  onHover: (a: Assignment | null, e?: { clientX: number; clientY: number }) => void;
  onOpen: (a: Assignment) => void;
  onBlockKey: (e: KeyboardEvent, a: Assignment) => void;
  onDragStart: (e: ReactPointerEvent, a: Assignment) => void;
}

function LaneRow(r: LaneRowProps) {
  const { lane, x, plotW } = r;
  const route = lane.route;
  const energyPct = route && route.energy_cap_wh > 0 ? Math.min(100, (route.energy_wh / route.energy_cap_wh) * 100) : null;
  const ss = ms(lane.shift_start_at);
  const se = ms(lane.shift_end_at);
  const bg = r.highlight ? "bg-sky-100" : r.selected ? "bg-sky-50" : r.index % 2 ? "bg-neutral-50/60" : "bg-white";
  return (
    <div
      data-drop-driver={lane.driver_id}
      className={`absolute left-0 flex border-b border-neutral-100 ${bg} ${lane.available ? "" : "text-neutral-400"}`}
      style={{ top: r.top, height: LANE_H, width: LABEL_W + r.extraW + plotW }}
    >
      <button
        type="button"
        onClick={r.onSelect}
        aria-pressed={r.selected}
        className="flex shrink-0 flex-col justify-center gap-0.5 border-r border-neutral-200 px-2 text-left hover:bg-sky-50"
        style={{ width: LABEL_W }}
        title={lane.unavailable_reason ?? lane.name}
      >
        <div className="flex min-w-0 items-center gap-1">
          <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: r.color }} />
          <span className={`truncate font-medium ${lane.available ? "text-neutral-900" : ""}`}>{lane.name}</span>
          {lane.vehicle_class && <span className="rounded border border-neutral-200 bg-neutral-100 px-0.5 text-[9px] uppercase text-neutral-600">{lane.vehicle_class}</span>}
          {r.diff && r.diff.gained > 0 && <span className="rounded bg-emerald-100 px-0.5 text-[9px] font-semibold text-emerald-800">+{r.diff.gained}</span>}
          {r.diff && r.diff.lost > 0 && <span className="rounded bg-red-100 px-0.5 text-[9px] font-semibold text-red-800">-{r.diff.lost}</span>}
          <span className="ml-auto shrink-0 text-[10px] text-neutral-500">{route?.trip_count ?? lane.assignments.length}</span>
        </div>
        <div className="flex min-w-0 items-center gap-1 text-[10px] text-neutral-500">
          {lane.available ? (
            <>
              <span className="truncate">
                {lane.vehicle_registration ?? "no vehicle"}
                {lane.vehicle_model ? ` ${lane.vehicle_model}` : ""}
              </span>
              {energyPct !== null && route && (
                <span className="ml-auto flex shrink-0 items-center gap-0.5" title={`Energy ${route.energy_wh} / ${route.energy_cap_wh} Wh`}>
                  <span className="inline-block h-1.5 w-10 overflow-hidden rounded bg-neutral-200">
                    <span className={`block h-full ${energyPct > 95 ? "bg-red-500" : energyPct > 80 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${energyPct}%` }} />
                  </span>
                  {Math.round(energyPct)}%
                </span>
              )}
            </>
          ) : (
            <span className="truncate text-red-700">unavailable: {lane.unavailable_reason ?? "unknown"}</span>
          )}
        </div>
      </button>
      {r.extraW > 0 && (
        <div className="flex shrink-0 items-center gap-1 overflow-hidden border-r border-neutral-200 px-1.5" style={{ width: r.extraW }}>
          {r.extra}
        </div>
      )}
      <svg width={plotW} height={LANE_H} className="shrink-0">
        {r.hours.map((h) => {
          const hx = x(r.t0 + h * 3600_000);
          return <line key={h} x1={hx} x2={hx} y1={0} y2={LANE_H} stroke={h % 6 === 0 ? "#d4d4d4" : "#f0f0f0"} />;
        })}
        {ss !== null && se !== null && (
          <rect x={x(ss)} y={4} width={Math.max(0, x(se) - x(ss))} height={LANE_H - 8} fill={lane.available ? "#e0f2fe" : "#e5e5e5"} rx={2} />
        )}
        {!lane.available && <rect x={0} y={0} width={plotW} height={LANE_H} fill="#a3a3a3" opacity={0.18} />}
        {lane.assignments.map((a) => (
          <TripBlock
            key={a.trip_id}
            a={a}
            x={x}
            changed={r.changedTrips.has(a.trip_id)}
            selected={r.selectedTripId === a.trip_id}
            picked={r.pickedTripId === a.trip_id}
            liveStatus={r.liveStatus}
            onHover={r.onHover}
            onOpen={r.onOpen}
            onKey={r.onBlockKey}
            onDragStart={r.onDragStart}
          />
        ))}
        {r.nowX !== null && <line x1={r.nowX} x2={r.nowX} y1={0} y2={LANE_H} stroke="#dc2626" strokeWidth={1.5} pointerEvents="none" />}
      </svg>
    </div>
  );
}

const LIVE_STATUS_FILL: Record<string, string> = {
  completed: "#a3a3a3",
  cancelled: "#d4d4d4",
  no_show: "#fca5a5",
};

function TripBlock({
  a,
  x,
  changed,
  selected,
  picked,
  liveStatus,
  onHover,
  onOpen,
  onKey,
  onDragStart,
}: {
  a: Assignment;
  x: (t: number) => number;
  changed: boolean;
  selected: boolean;
  picked: boolean;
  liveStatus?: boolean;
  onHover: LaneRowProps["onHover"];
  onOpen: (a: Assignment) => void;
  onKey: (e: KeyboardEvent, a: Assignment) => void;
  onDragStart: (e: ReactPointerEvent, a: Assignment) => void;
}) {
  const pu = ms(a.planned_pickup_at);
  const dr = ms(a.planned_drop_at);
  if (pu === null || dr === null) return null;
  const dep = ms(a.planned_depart_at);
  const arr = ms(a.planned_arrive_pickup_at);
  const x1 = x(pu);
  const x2 = Math.max(x1 + 3, x(dr));
  const w = x2 - x1;
  const st = a.status ?? "";
  const live = !!liveStatus;
  const color = (live && LIVE_STATUS_FILL[st]) || tripColor(a.trip_type);
  const inProgress = live && (st === "started" || st === "en_route" || st === "arrived");
  const bufStart = a.buffer_s ? pu - a.buffer_s * 1000 : null;
  const stroke = picked ? "#0ea5e9" : selected ? "#111827" : changed ? "#d97706" : inProgress ? (st === "started" ? "#facc15" : "#111827") : "none";
  const label = `${fmtTime(a.planned_pickup_at)} ${a.trip_type ?? "trip"}${a.vip ? " VIP" : ""}${a.locked ? " locked" : ""}`;
  return (
    <g
      tabIndex={0}
      role="button"
      aria-label={`Trip ${label}, pickup ${a.pickup_address ?? ""}. Enter for details, Space to move`}
      className="cursor-grab outline-none focus-visible:[&>rect.block]:stroke-sky-500"
      onPointerDown={(e) => onDragStart(e, a)}
      onClick={() => onOpen(a)}
      onKeyDown={(e) => onKey(e, a)}
      onMouseMove={(e) => onHover(a, e)}
      onMouseLeave={() => onHover(null)}
      onFocus={(e) => {
        const r = (e.currentTarget as SVGGElement).getBoundingClientRect();
        onHover(a, { clientX: r.left + r.width / 2, clientY: r.bottom });
      }}
      onBlur={() => onHover(null)}
    >
      {dep !== null && arr !== null && arr > dep && <line x1={x(dep)} x2={x(arr)} y1={LANE_H / 2} y2={LANE_H / 2} stroke="#737373" strokeWidth={1.5} strokeDasharray="1 0" />}
      {dep !== null && arr !== null && arr > dep && <circle cx={x(dep)} cy={LANE_H / 2} r={1.8} fill="#737373" />}
      {bufStart !== null && x1 - x(bufStart) > 0.5 && <rect x={x(bufStart)} y={14} width={x1 - x(bufStart)} height={LANE_H - 28} fill="url(#tl-hatch)" />}
      <rect className="block" x={x1} y={11} width={w} height={LANE_H - 22} rx={2} fill={color} opacity={st === "cancelled" ? 0.35 : live && st === "completed" ? 0.6 : 0.92} stroke={stroke} strokeWidth={stroke === "none" ? 0 : inProgress && st === "started" ? 3 : 2} strokeDasharray={inProgress && st !== "started" ? "3 2" : undefined} />
      {w > 34 && (
        <text x={x1 + 3} y={LANE_H / 2 + 3.5} fontSize={9.5} fill="#fff" pointerEvents="none">
          {fmtTime(a.planned_pickup_at)}
        </text>
      )}
      {a.vip && <circle cx={x1 + 1} cy={11} r={3.2} fill="#dc2626" stroke="#fff" strokeWidth={1} pointerEvents="none" />}
      {a.locked && <LockGlyph x={x2 - 4} y={8} />}
    </g>
  );
}

function LockGlyph({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x - 4},${y - 2})`} pointerEvents="none">
      <rect x={0.5} y={4} width={7} height={5.5} rx={1} fill="#111827" stroke="#fff" strokeWidth={0.8} />
      <path d="M2 4 V2.6 a2 2 0 0 1 4 0 V4" fill="none" stroke="#111827" strokeWidth={1.3} />
    </g>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="w-20 shrink-0 text-neutral-400">{k}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

function Tooltip({ h }: { h: Hover }) {
  const a = h.a;
  const left = Math.min(h.x + 14, window.innerWidth - 300);
  const top = Math.min(h.y + 14, window.innerHeight - 220);
  return (
    <div className="pointer-events-none fixed z-[80] w-72 rounded border border-neutral-300 bg-white p-2 text-[11px] shadow-lg" style={{ left, top }}>
      <div className="mb-1 flex items-center gap-1 font-semibold">
        <span className="inline-block h-2 w-2 rounded-sm" style={{ background: tripColor(a.trip_type) }} />
        {a.trip_type ?? "trip"} {a.channel ? `(${a.channel})` : ""}
        {a.vip && <span className="rounded bg-red-100 px-1 text-[9px] text-red-800">VIP</span>}
        {a.locked && <span className="rounded bg-neutral-800 px-1 text-[9px] text-white">locked</span>}
      </div>
      <Row k="Driver">{h.lane.name}</Row>
      <Row k="Scheduled">{fmtTime(a.scheduled_pickup_at)}</Row>
      <Row k="Depart">{fmtTime(a.planned_depart_at)}</Row>
      <Row k="At pickup">{fmtTime(a.planned_arrive_pickup_at)}</Row>
      <Row k="Pickup">
        {fmtTime(a.planned_pickup_at)} {a.pickup_address ?? ""}
      </Row>
      <Row k="Drop">
        {fmtTime(a.planned_drop_at)} {a.drop_address ?? ""}
      </Row>
      <Row k="Deadhead">
        {secToMin(a.deadhead_s)} min ({a.deadhead_source ?? "-"})
      </Row>
      <Row k="Buffer">{secToMin(a.buffer_s)} min</Row>
      <Row k="Trip">
        {secToMin(a.trip_s)} min, p50 {secToMin(a.trip_p50_s)} ({a.trip_source ?? "-"})
      </Row>
      <Row k="Slack">{secToMin(a.slack_s)} min</Row>
      {a.account_id && <Row k="Account">{a.account_id}</Row>}
    </div>
  );
}
