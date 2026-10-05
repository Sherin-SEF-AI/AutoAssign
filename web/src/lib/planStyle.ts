// Shared colours for the plan timeline and the map.

export const TRIP_TYPE_COLOR: Record<string, string> = {
  ets: "#2563eb",
  airport: "#7c3aed",
  city: "#059669",
  package: "#ea580c",
};

export function tripColor(type: string | null | undefined): string {
  return (type && TRIP_TYPE_COLOR[type]) || "#525252";
}

const DRIVER_PALETTE = [
  "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf",
  "#8c564b", "#e377c2", "#bcbd22", "#3b4cc0", "#b40426", "#006d5b",
  "#7f3b08", "#542788", "#c51b7d", "#4d9221",
];

export function driverColor(driverId: string, index?: number): string {
  if (index !== undefined) return DRIVER_PALETTE[index % DRIVER_PALETTE.length] ?? "#333";
  let h = 0;
  for (let i = 0; i < driverId.length; i++) h = (h * 31 + driverId.charCodeAt(i)) >>> 0;
  return DRIVER_PALETTE[h % DRIVER_PALETTE.length] ?? "#333";
}

export const REASON_LABEL: Record<string, string> = {
  no_eligible_vehicle: "no eligible vehicle",
  shift: "shift",
  energy_cap: "energy cap",
  time_window: "time window",
  ops_unassigned: "ops unassigned",
};

export const REASON_TONE: Record<string, string> = {
  no_eligible_vehicle: "bg-red-50 text-red-800 border-red-200",
  shift: "bg-amber-50 text-amber-800 border-amber-200",
  energy_cap: "bg-orange-50 text-orange-800 border-orange-200",
  time_window: "bg-violet-50 text-violet-800 border-violet-200",
  ops_unassigned: "bg-neutral-100 text-neutral-700 border-neutral-300",
};
