import {
  QueryCache,
  MutationCache,
  QueryClient,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, rawGet, unwrap, type Schemas } from "../api/client";
import { ApiError } from "../api/errors";
import { toast } from "../components/Toast";
import { setAuth } from "./auth";

export type TripRow = Schemas["TripRow"];
export type TripDetail = Schemas["TripDetailOut"];
export type Driver = Schemas["DriverOut"];
export type Vehicle = Schemas["VehicleOut"];
export type Hub = Schemas["HubOut"];
export type Snapshot = Schemas["SnapshotOut"];
export type JobInfo = Schemas["JobInfo"];
export type JobRun = Schemas["JobRunOut"];

export const TRIP_STATUSES = [
  "booked",
  "assigned",
  "en_route",
  "arrived",
  "started",
  "completed",
  "cancelled",
  "no_show",
] as const;

export const JOB_TERMINAL = new Set(["succeeded", "failed", "skipped"]);

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { label?: string; silent?: boolean };
  }
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache(),
    mutationCache: new MutationCache({
      // Nothing fails silently: every mutation error surfaces its code and message.
      onError: (error, _vars, _ctx, mutation) => {
        if (mutation.meta?.silent) return;
        toast.error(error, mutation.meta?.label);
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        retry: (count, err) => {
          if (err instanceof ApiError && err.status >= 400 && err.status < 500) return false;
          return count < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}

/* ---------------- auth ---------------- */

export function useLogin() {
  return useMutation({
    meta: { label: "Login" },
    mutationFn: (body: Schemas["LoginIn"]) => unwrap(api.POST("/api/v1/auth/login", { body })),
    onSuccess: (out) => setAuth(out.access_token, out.role, out.email),
  });
}

export function useMe(enabled: boolean) {
  return useQuery({
    queryKey: ["me"],
    enabled,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/auth/me", { signal })),
    staleTime: 5 * 60_000,
  });
}

/* ---------------- placeholders for endpoints not built yet ---------------- */

export interface PlanSummary {
  plan_id: string;
  version: number;
  status: string;
  created_at?: string;
}

/** GET /api/v1/plans?date=. Not implemented on the backend yet; callers treat failure as "no plans". */
export function usePlans(date: string) {
  return useQuery({
    queryKey: ["plans", date],
    queryFn: async ({ signal }) => {
      const r = await rawGet<PlanSummary[] | { items: PlanSummary[] }>(`/api/v1/plans?date=${encodeURIComponent(date)}`, signal);
      return Array.isArray(r) ? r : (r.items ?? []);
    },
    retry: false,
    staleTime: 60_000,
  });
}

export interface Budget {
  provider?: string;
  used?: number;
  limit?: number;
  pct?: number;
  [k: string]: unknown;
}

/** GET /api/v1/admin/budget. Not implemented yet; hidden on failure. */
export function useBudget(enabled = true) {
  return useQuery({
    queryKey: ["budget"],
    enabled,
    queryFn: ({ signal }) => rawGet<Budget | Budget[]>("/api/v1/admin/budget", signal),
    retry: false,
    staleTime: 60_000,
  });
}

/* ---------------- trips ---------------- */

export function useTrips(date: string, q: string, status: string, limit = 100) {
  return useInfiniteQuery({
    queryKey: ["trips", "list", { date, q, status, limit }],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET("/api/v1/trips", {
          params: {
            query: {
              date,
              limit,
              ...(pageParam ? { cursor: pageParam } : {}),
              ...(q ? { q } : {}),
              ...(status ? { status } : {}),
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? null,
    placeholderData: keepPreviousData,
  });
}

export function useTrip(tripId: string | null) {
  return useQuery({
    queryKey: ["trips", "detail", tripId],
    enabled: !!tripId,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/trips/{trip_id}", { params: { path: { trip_id: tripId ?? "" } }, signal })),
  });
}

/* ---------------- fleet ---------------- */

export function useDrivers(date: string) {
  return useQuery({
    queryKey: ["fleet", "drivers", date],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/drivers", { params: { query: { date } }, signal })),
  });
}

export function useVehicles(date: string) {
  return useQuery({
    queryKey: ["fleet", "vehicles", date],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/vehicles", { params: { query: { date } }, signal })),
  });
}

export function useHubs() {
  return useQuery({
    queryKey: ["fleet", "hubs"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/hubs", { signal })),
    staleTime: 10 * 60_000,
  });
}

export function useSocCheckin() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "SOC check-in" },
    mutationFn: (v: { vehicleId: string; body: Schemas["SocCheckinIn"] }) =>
      unwrap(
        api.POST("/api/v1/vehicles/{vehicle_id}/soc-checkin", {
          params: { path: { vehicle_id: v.vehicleId } },
          body: v.body,
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["fleet"] });
    },
  });
}

/* ---------------- data ---------------- */

export function useSnapshots(date: string | null) {
  return useQuery({
    queryKey: ["snapshots", date],
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/snapshots", { params: { query: date ? { date } : {} }, signal })),
  });
}

export function useRegenerate() {
  return useMutation({
    meta: { label: "Regenerate" },
    mutationFn: (body: Schemas["RegenerateIn"]) =>
      unwrap(api.POST("/api/v1/admin/synthetic/regenerate", { body })),
  });
}

/* ---------------- jobs ---------------- */

export function useJobs() {
  return useQuery({
    queryKey: ["jobs", "list"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/jobs", { signal })),
    staleTime: 5 * 60_000,
  });
}

export function useJobRuns(name: string, limit = 50) {
  return useInfiniteQuery({
    queryKey: ["jobs", "runs", { name, limit }],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET("/api/v1/jobs/runs", {
          params: { query: { limit, ...(name ? { name } : {}), ...(pageParam ? { cursor: pageParam } : {}) } },
          signal,
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? null,
    refetchInterval: 5000,
    placeholderData: keepPreviousData,
  });
}

/** Poll a single job run every second until it reaches a terminal state. */
export function useJobRun(runId: string | null) {
  return useQuery({
    queryKey: ["jobs", "run", runId],
    enabled: !!runId,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/runs/{run_id}", { params: { path: { run_id: runId ?? "" } }, signal })),
    refetchInterval: (q) => (q.state.data && JOB_TERMINAL.has(q.state.data.status) ? false : 1000),
    // A freshly accepted run may not be visible for a moment; keep polling through 404s.
    retry: (count, err) => (err instanceof ApiError && err.status === 404 ? count < 5 : count < 2),
    retryDelay: 1000,
  });
}

export function useRunJob() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "Run job" },
    mutationFn: (v: { name: string; serviceDate: string | null }) =>
      unwrap(
        api.POST("/api/v1/jobs/{name}/run", {
          params: { path: { name: v.name } },
          body: v.serviceDate ? { service_date: v.serviceDate } : {},
        }),
      ),
    onSuccess: (out) => {
      toast.success(`Started ${out.job_name}`, `run ${out.run_id.slice(0, 8)}${out.service_date ? ` for ${out.service_date}` : ""}`);
      void qc.invalidateQueries({ queryKey: ["jobs", "runs"] });
    },
  });
}
