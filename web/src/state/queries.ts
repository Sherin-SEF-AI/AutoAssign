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
import { api, unwrap, type Schemas } from "../api/client";
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
export type Budget = Schemas["BudgetOut"];
export type Estimates = Schemas["EstimatesOut"];
export type SettingsOut = Schemas["SettingsOut"];
export type Audit = Schemas["AuditOut"];
export type Factors = Schemas["FactorsOut"];
export type Factor = Schemas["FactorOut"];
export type PlanSummary = Schemas["PlanSummary"];
export type PlanDetail = Schemas["PlanDetail"];
export type DriverLane = Schemas["DriverLane"];
export type Assignment = Schemas["AssignmentOut"];
export type MoveIn = Schemas["MoveIn"];
export type MoveOut = Schemas["MoveOut"];
export type Issue = Schemas["IssueOut"];
export type PlanDiff = Schemas["DiffOut"];

export const TRIP_STATUSES = [
  "booked",
  "assigned",
  "en_route",
  "arrived",
  "started",
  "completed",
  "cancelled",
  "no_show",
] as const satisfies readonly TripRow["status"][];

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

/* ---------------- plans ---------------- */

export function usePlans(date: string) {
  return useQuery({
    queryKey: ["plans", date],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/plans", { params: { query: { date } }, signal })),
    retry: false,
    staleTime: 30_000,
  });
}

export function usePlan(planId: string | null) {
  return useQuery({
    queryKey: ["plan", planId],
    enabled: !!planId,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/plans/{plan_id}", { params: { path: { plan_id: planId ?? "" } }, signal })),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function usePlanDiff(planId: string | null, against: string | null) {
  return useQuery({
    queryKey: ["plan", planId, "diff", against],
    enabled: !!planId && !!against && planId !== against,
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/plans/{plan_id}/diff", {
          params: { path: { plan_id: planId ?? "" }, query: { against: against ?? "" } },
          signal,
        }),
      ),
    staleTime: 5 * 60_000,
  });
}

export function useRepairs(date: string) {
  return useQuery({
    queryKey: ["repairs", date],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/repairs", { params: { query: { date } }, signal })),
    retry: false,
  });
}

/** Put a freshly created version at the top of the cached list so the selector shows it at once. */
export function adoptPlan(qc: QueryClient, p: PlanSummary): void {
  qc.setQueryData<PlanSummary[]>(["plans", p.service_date], (old) => [p, ...(old ?? []).filter((x) => x.plan_id !== p.plan_id)]);
  void qc.invalidateQueries({ queryKey: ["plans"] });
  void qc.invalidateQueries({ queryKey: ["trips"] });
  void qc.invalidateQueries({ queryKey: ["fleet", "drivers"] });
}

export function useSolve() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "Solve" },
    mutationFn: (serviceDate: string) => unwrap(api.POST("/api/v1/plans/solve", { body: { service_date: serviceDate } })),
    onSuccess: (p) => adoptPlan(qc, p),
  });
}

export function useMove() {
  return useMutation({
    meta: { label: "Move" },
    mutationFn: (v: { planId: string; body: MoveIn }) =>
      unwrap(api.POST("/api/v1/plans/{plan_id}/moves", { params: { path: { plan_id: v.planId } }, body: v.body })),
  });
}

export function useLock() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "Lock" },
    mutationFn: (v: { planId: string; tripId: string; lock: boolean }) => {
      const params = { path: { plan_id: v.planId, trip_id: v.tripId } };
      return unwrap(
        v.lock
          ? api.POST("/api/v1/plans/{plan_id}/assignments/{trip_id}/lock", { params })
          : api.DELETE("/api/v1/plans/{plan_id}/assignments/{trip_id}/lock", { params }),
      );
    },
    onSuccess: (p) => adoptPlan(qc, p),
  });
}

export function usePublish() {
  const qc = useQueryClient();
  return useMutation({
    // The page handles unassigned_not_acknowledged itself and toasts everything else.
    meta: { label: "Publish", silent: true },
    mutationFn: (planId: string) => unwrap(api.POST("/api/v1/plans/{plan_id}/publish", { params: { path: { plan_id: planId } } })),
    onSuccess: (p) => {
      qc.setQueryData<PlanSummary[]>(["plans", p.service_date], (old) => (old ?? []).map((x) => (x.plan_id === p.plan_id ? p : x.status === "published" ? { ...x, status: "superseded" } : x)));
      void qc.invalidateQueries({ queryKey: ["plans"] });
      void qc.invalidateQueries({ queryKey: ["plan"] });
    },
  });
}

export function useAcknowledge() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "Acknowledge" },
    mutationFn: (v: { planId: string; tripIds: string[] | null; note: string }) =>
      unwrap(
        api.POST("/api/v1/plans/{plan_id}/unassigned/acknowledge", {
          params: { path: { plan_id: v.planId } },
          body: { trip_ids: v.tripIds, note: v.note },
        }),
      ),
    onSuccess: (_out, v) => {
      void qc.invalidateQueries({ queryKey: ["plan", v.planId] });
    },
  });
}

export function useMapStyle() {
  return useQuery({
    queryKey: ["map", "style"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/map/style.json", { signal })),
    retry: false,
    staleTime: 10 * 60_000,
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

/* ---------------- provider budget ---------------- */

export function useBudget() {
  return useQuery({
    queryKey: ["budget"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/admin/budget", { signal })),
    refetchInterval: 30_000,
    retry: false,
  });
}

/* ---------------- estimates ---------------- */

export function useEstimates(date: string) {
  return useQuery({
    queryKey: ["estimates", date],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/admin/estimates", { params: { query: { date } }, signal })),
  });
}

/* ---------------- settings ---------------- */

export function useSettings() {
  return useQuery({
    queryKey: ["settings", "values"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/settings", { signal })),
  });
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "Save settings" },
    mutationFn: (changes: Record<string, unknown>) => unwrap(api.PUT("/api/v1/settings", { body: changes })),
    onSuccess: (out) => {
      qc.setQueryData(["settings", "values"], out);
      void qc.invalidateQueries({ queryKey: ["settings"] });
      void qc.invalidateQueries({ queryKey: ["budget"] });
    },
  });
}

export function useSettingsAudit() {
  return useQuery({
    queryKey: ["settings", "audit"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/settings/audit", { signal })),
  });
}

export function useFactors() {
  return useQuery({
    queryKey: ["settings", "factors"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/settings/factors", { signal })),
    staleTime: 5 * 60_000,
  });
}

/* ---------------- live monitor ---------------- */

export type LiveOut = Schemas["LiveOut"];
export type LiveDriver = Schemas["LiveDriver"];
export type Repair = Schemas["RepairOut"];
export type SimStatus = Schemas["SimStatus"];

export function useLive(date: string) {
  return useQuery({
    queryKey: ["live", date],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/live", { params: { query: { date } }, signal })),
    refetchInterval: 30_000,
  });
}

export function useResolve() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "Re-solve" },
    mutationFn: (v: { planId: string; driverIds: string[]; reason?: string; includeFloat?: boolean }) =>
      unwrap(
        api.POST("/api/v1/plans/{plan_id}/resolve", {
          params: { path: { plan_id: v.planId } },
          body: { driver_ids: v.driverIds, reason: v.reason ?? "manual re-solve", include_float: v.includeFloat ?? true },
        }),
      ),
    onSuccess: (out) => {
      if (out.plan) adoptPlan(qc, out.plan);
      void qc.invalidateQueries({ queryKey: ["repairs"] });
      void qc.invalidateQueries({ queryKey: ["live"] });
    },
  });
}

/* ---------------- simulator ---------------- */

export function useSimStatus() {
  return useQuery({
    queryKey: ["sim", "status"],
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/admin/sim/status", { signal })),
    refetchInterval: (q) => (q.state.error ? 60_000 : 5_000),
    retry: false,
    staleTime: 2_000,
  });
}

type SimAction = { kind: "start"; serviceDate: string | null; speed: number | null } | { kind: "pause" } | { kind: "reset" } | { kind: "disturbances"; flags: Schemas["DisturbancesIn"] };

export function useSimControl() {
  const qc = useQueryClient();
  return useMutation({
    meta: { label: "Simulator" },
    mutationFn: (a: SimAction) => {
      switch (a.kind) {
        case "start":
          return unwrap(api.POST("/api/v1/admin/sim/start", { body: { service_date: a.serviceDate, speed: a.speed } }));
        case "pause":
          return unwrap(api.POST("/api/v1/admin/sim/pause"));
        case "reset":
          return unwrap(api.POST("/api/v1/admin/sim/reset"));
        case "disturbances":
          return unwrap(api.POST("/api/v1/admin/sim/disturbances", { body: a.flags }));
      }
    },
    onSuccess: (st) => {
      qc.setQueryData(["sim", "status"], st);
    },
  });
}
