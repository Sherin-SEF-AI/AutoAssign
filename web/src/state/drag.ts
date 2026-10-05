import { useSyncExternalStore, type PointerEvent as ReactPointerEvent } from "react";

/**
 * Pointer based drag and drop shared by the timeline and the unassigned tray.
 * Drop targets are any elements carrying `data-drop-driver`: a driver id, or "" for "unassign".
 */
export interface DragState {
  tripId: string;
  fromDriverId: string | null;
  label: string;
  x: number;
  y: number;
  /** driver id, null for the unassign tray, undefined when not over a target */
  over: string | null | undefined;
}

let state: DragState | null = null;
let lastDragEndedAt = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function set(s: DragState | null) {
  state = s;
  emit();
}

export function useDragState(): DragState | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
    () => state,
    () => state,
  );
}

/** True right after a drag finished, so the click that follows a drop can be ignored. */
export function suppressClick(): boolean {
  return Date.now() - lastDragEndedAt < 250;
}

function targetAt(x: number, y: number): string | null | undefined {
  const el = document.elementFromPoint(x, y);
  const t = el instanceof Element ? el.closest("[data-drop-driver]") : null;
  if (!t) return undefined;
  const v = t.getAttribute("data-drop-driver") ?? "";
  return v === "" ? null : v;
}

export function startPointerDrag(
  e: ReactPointerEvent,
  info: { tripId: string; fromDriverId: string | null; label: string },
  onDrop: (tripId: string, toDriverId: string | null) => void,
): void {
  if (e.button !== 0) return;
  const sx = e.clientX;
  const sy = e.clientY;
  let active = false;

  const move = (ev: PointerEvent) => {
    if (!active) {
      if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 5) return;
      active = true;
    }
    ev.preventDefault();
    set({ ...info, x: ev.clientX, y: ev.clientY, over: targetAt(ev.clientX, ev.clientY) });
  };
  const cleanup = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", cancel);
    window.removeEventListener("keydown", key);
  };
  const up = (ev: PointerEvent) => {
    cleanup();
    if (!active) return;
    lastDragEndedAt = Date.now();
    const over = targetAt(ev.clientX, ev.clientY);
    set(null);
    if (over !== undefined && over !== info.fromDriverId) onDrop(info.tripId, over);
  };
  const cancel = () => {
    cleanup();
    if (active) lastDragEndedAt = Date.now();
    set(null);
  };
  const key = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") cancel();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", cancel);
  window.addEventListener("keydown", key);
}

