import { useEffect, useMemo, useRef, useState } from "react";
import { Map as MlMap, NavigationControl, LngLatBounds, setWorkerUrl, type GeoJSONSource, type StyleSpecification, type ExpressionSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 locates its worker relative to its own module, which breaks once bundled.
// Let Vite build the worker (with its shared chunk) as a standalone ES module and point MapLibre at it.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(maplibreWorkerUrl);
import { API_BASE } from "../api/client";
import { getToken } from "../state/auth";
import { useHubs, useMapStyle, type Assignment, type DriverLane } from "../state/queries";

/* MapLibre view on the HERE basemap (proxied by the API). Overlays: routes, pickups, drops, hubs. */

const BLR: [number, number] = [77.5946, 12.9716];
const TILE_PATH = "/api/v1/map/tiles/";

export interface MapViewProps {
  lanes: DriverLane[];
  unassigned: Assignment[];
  selectedDriverId: string | null;
  onSelectDriver: (driverId: string | null) => void;
  colorFor: (driverId: string) => string;
  /** changes when a different plan is shown, to refit the view once */
  fitKey: string | null;
  /** Live driver positions, coloured by risk. */
  live?: { driver_id: string; lat: number; lng: number; risk: string }[];
  /** Draw drop points (default true). */
  showDrops?: boolean;
}

const RISK_COLOR: Record<string, string> = { green: "#16a34a", amber: "#f59e0b", red: "#dc2626" };

type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;
const fc = (features: Feature[]): GeoJSON.FeatureCollection<GeoJSON.Geometry, Record<string, unknown>> => ({ type: "FeatureCollection", features });

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function absolutise(url: string): string {
  if (/^https?:\/\//.test(url)) return url;
  return new URL(API_BASE + url, window.location.href).toString();
}

/** Turn the API style into one MapLibre can load: absolute tile URLs. Falls back to a plain background. */
function prepareStyle(raw: Record<string, unknown> | undefined): { style: StyleSpecification; hereEnabled: boolean; attribution: string } {
  const fallbackAttr = `© ${new Date().getFullYear()} HERE`;
  if (!raw || !isRecord(raw.sources) || !Array.isArray(raw.layers)) {
    return {
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#eef0f2" } }] },
      hereEnabled: false,
      attribution: fallbackAttr,
    };
  }
  const sources: Record<string, unknown> = {};
  for (const [id, src] of Object.entries(raw.sources)) {
    if (isRecord(src) && Array.isArray(src.tiles)) sources[id] = { ...src, tiles: src.tiles.map((t) => absolutise(String(t))) };
    else sources[id] = src;
  }
  const meta = isRecord(raw.metadata) ? raw.metadata : {};
  const here = isRecord(raw.sources.here) ? raw.sources.here : {};
  const attribution = typeof here.attribution === "string" ? here.attribution : typeof meta.attribution === "string" ? meta.attribution : fallbackAttr;
  const hereEnabled = meta.here_enabled !== false;
  let layers = raw.layers as unknown[];
  if (!hereEnabled) {
    // No tile requests while HERE is off: keep the background and our own overlays only.
    delete sources.here;
    layers = layers.filter((l) => !(isRecord(l) && l.source === "here"));
  }
  return {
    style: { ...(raw as unknown as StyleSpecification), sources: sources as StyleSpecification["sources"], layers: layers as StyleSpecification["layers"] },
    hereEnabled,
    attribution,
  };
}

function laneCoords(l: DriverLane): [number, number][] {
  const pts: [number, number][] = [[l.start_lng, l.start_lat]];
  const as = [...l.assignments].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  for (const a of as) {
    if (a.pickup_lat != null && a.pickup_lng != null) pts.push([a.pickup_lng, a.pickup_lat]);
    if (a.drop_lat != null && a.drop_lng != null) pts.push([a.drop_lng, a.drop_lat]);
  }
  pts.push([l.end_lng, l.end_lat]);
  return pts;
}

export default function MapView({ lanes, unassigned, selectedDriverId, onSelectDriver, colorFor, fitKey, live, showDrops = true }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);
  const styleQ = useMapStyle();
  const hubs = useHubs();
  const prepared = useMemo(() => (styleQ.isLoading ? null : prepareStyle(styleQ.data as Record<string, unknown> | undefined)), [styleQ.isLoading, styleQ.data]);
  const onSelectRef = useRef(onSelectDriver);
  onSelectRef.current = onSelectDriver;
  const fittedFor = useRef<string | null>(null);

  // Create the map once the style is known.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !prepared) return;
    const map = new MlMap({
      container: el,
      style: prepared.style,
      center: BLR,
      zoom: 10.5,
      attributionControl: { compact: false, customAttribution: prepared.attribution },
      transformRequest: (url) => {
        const token = getToken();
        if (token && url.includes(TILE_PATH)) {
          return { url: `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` };
        }
        return { url };
      },
    });
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    // Tile failures (503 here_unavailable) are expected when HERE is off: keep them out of the console.
    map.on("error", () => undefined);
    map.on("load", () => {
      map.addSource("routes", { type: "geojson", data: fc([]) });
      map.addSource("points", { type: "geojson", data: fc([]) });
      map.addSource("hubs", { type: "geojson", data: fc([]) });
      map.addSource("live", { type: "geojson", data: fc([]) });
      map.addLayer({
        id: "routes",
        type: "line",
        source: "routes",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": ["get", "color"], "line-width": 2, "line-opacity": 0.35 },
      });
      map.addLayer({
        id: "points",
        type: "circle",
        source: "points",
        paint: {
          "circle-radius": ["match", ["get", "kind"], "unassigned", 5, 3.5],
          "circle-color": ["match", ["get", "kind"], "pickup", "#16a34a", "drop", "#dc2626", "unassigned", "#f97316", "#525252"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
          "circle-opacity": 0.8,
          "circle-stroke-opacity": 0.8,
        },
      });
      map.addLayer({
        id: "hubs",
        type: "circle",
        source: "hubs",
        paint: { "circle-radius": 6, "circle-color": "#111827", "circle-stroke-color": "#facc15", "circle-stroke-width": 2 },
      });
      map.addLayer({
        id: "live",
        type: "circle",
        source: "live",
        paint: {
          "circle-radius": 7,
          "circle-color": ["get", "color"],
          "circle-stroke-color": "#111827",
          "circle-stroke-width": 1.5,
        },
      });
      map.on("click", "live", (e) => {
        const id = e.features?.[0]?.properties?.driver_id;
        if (typeof id === "string") onSelectRef.current(id);
      });
      map.on("click", "routes", (e) => {
        const id = e.features?.[0]?.properties?.driver_id;
        if (typeof id === "string") onSelectRef.current(id);
      });
      map.on("mouseenter", "routes", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "routes", () => (map.getCanvas().style.cursor = ""));
      setReady(true);
    });
    mapRef.current = map;
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      setReady(false);
      mapRef.current = null;
      map.remove();
    };
  }, [prepared]);

  // Data.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const routes: Feature[] = [];
    const points: Feature[] = [];
    for (const l of lanes) {
      if (l.assignments.length === 0) continue;
      routes.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: laneCoords(l) },
        properties: { driver_id: l.driver_id, color: colorFor(l.driver_id) },
      });
      for (const a of l.assignments) {
        if (a.pickup_lat != null && a.pickup_lng != null)
          points.push({ type: "Feature", geometry: { type: "Point", coordinates: [a.pickup_lng, a.pickup_lat] }, properties: { kind: "pickup", driver_id: l.driver_id } });
        if (showDrops && a.drop_lat != null && a.drop_lng != null)
          points.push({ type: "Feature", geometry: { type: "Point", coordinates: [a.drop_lng, a.drop_lat] }, properties: { kind: "drop", driver_id: l.driver_id } });
      }
    }
    for (const a of unassigned) {
      if (a.pickup_lat != null && a.pickup_lng != null)
        points.push({ type: "Feature", geometry: { type: "Point", coordinates: [a.pickup_lng, a.pickup_lat] }, properties: { kind: "unassigned", driver_id: "" } });
    }
    const hubF: Feature[] = (hubs.data ?? []).map((h) => ({ type: "Feature", geometry: { type: "Point", coordinates: [h.lng, h.lat] }, properties: { name: h.name } }));
    (map.getSource("routes") as GeoJSONSource | undefined)?.setData(fc(routes));
    (map.getSource("points") as GeoJSONSource | undefined)?.setData(fc(points));
    (map.getSource("hubs") as GeoJSONSource | undefined)?.setData(fc(hubF));
    const liveF: Feature[] = (live ?? []).map((d) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [d.lng, d.lat] },
      properties: { driver_id: d.driver_id, color: RISK_COLOR[d.risk] ?? "#737373" },
    }));
    (map.getSource("live") as GeoJSONSource | undefined)?.setData(fc(liveF));

    if (fitKey && fittedFor.current !== fitKey && (routes.length > 0 || points.length > 0)) {
      const b = new LngLatBounds();
      for (const r of routes) for (const c of (r.geometry as GeoJSON.LineString).coordinates) b.extend([c[0] ?? BLR[0], c[1] ?? BLR[1]]);
      for (const p of points) {
        const c = (p.geometry as GeoJSON.Point).coordinates;
        b.extend([c[0] ?? BLR[0], c[1] ?? BLR[1]]);
      }
      if (!b.isEmpty()) map.fitBounds(b, { padding: 30, maxZoom: 13, duration: 0 });
      fittedFor.current = fitKey;
    }
  }, [ready, lanes, unassigned, hubs.data, colorFor, fitKey, live, showDrops]);

  // Selection styling.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const sel = selectedDriverId ?? "";
    const isSel: ExpressionSpecification = ["==", ["get", "driver_id"], sel];
    map.setPaintProperty("routes", "line-opacity", selectedDriverId ? ["case", isSel, 0.95, 0.07] : 0.35);
    map.setPaintProperty("routes", "line-width", selectedDriverId ? ["case", isSel, 4, 1.5] : 2);
    const pOpacity: ExpressionSpecification | number = selectedDriverId ? ["case", ["any", isSel, ["==", ["get", "kind"], "unassigned"]], 0.95, 0.12] : 0.8;
    map.setPaintProperty("points", "circle-opacity", pOpacity);
    map.setPaintProperty("points", "circle-stroke-opacity", pOpacity);
    map.setPaintProperty("live", "circle-opacity", selectedDriverId ? ["case", isSel, 1, 0.35] : 1);
    map.setPaintProperty("live", "circle-stroke-opacity", selectedDriverId ? ["case", isSel, 1, 0.35] : 1);
    if (selectedDriverId) {
      const lane = lanes.find((l) => l.driver_id === selectedDriverId);
      if (lane && lane.assignments.length > 0) {
        const b = new LngLatBounds();
        for (const c of laneCoords(lane)) b.extend(c);
        map.fitBounds(b, { padding: 40, maxZoom: 14, duration: 300 });
      }
    }
  }, [ready, selectedDriverId, lanes]);

  return (
    <div className="relative h-full min-h-[320px] w-full overflow-hidden rounded border border-neutral-200 bg-[#eef0f2]">
      {/* Inline position: maplibre-gl.css sets .maplibregl-map to relative and would collapse this box. */}
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      {prepared && !prepared.hereEnabled && (
        <div className="absolute left-2 top-2 z-[1] rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-800">HERE tiles unavailable</div>
      )}
      <div className="absolute bottom-7 left-2 z-[1] flex flex-wrap gap-2 rounded bg-white/90 px-1.5 py-0.5 text-[10px] text-neutral-700 shadow-sm">
        <span><span className="mr-0.5 inline-block h-2 w-2 rounded-full bg-green-600" />pickup</span>
        {showDrops && <span><span className="mr-0.5 inline-block h-2 w-2 rounded-full bg-red-600" />drop</span>}
        <span><span className="mr-0.5 inline-block h-2 w-2 rounded-full bg-orange-500" />unassigned</span>
        <span><span className="mr-0.5 inline-block h-2 w-2 rounded-full border border-yellow-400 bg-neutral-900" />hub</span>
        {live && <span><span className="mr-0.5 inline-block h-2 w-2 rounded-full border border-neutral-900 bg-amber-500" />driver (risk)</span>}
      </div>
      {selectedDriverId && (
        <button type="button" onClick={() => onSelectDriver(null)} className="absolute right-12 top-2 z-[1] rounded border border-neutral-300 bg-white px-1.5 py-0.5 text-[11px] shadow-sm">
          Show all drivers
        </button>
      )}
    </div>
  );
}
