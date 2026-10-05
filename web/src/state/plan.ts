import { useServiceDate, useSearchParam } from "./date";
import { usePlans, type PlanSummary } from "./queries";

/**
 * Selected plan version for the current service date, kept in the `plan` URL param.
 * Default: the published version, else the newest (the list is newest first).
 */
export function useSelectedPlan() {
  const [date] = useServiceDate();
  const plans = usePlans(date);
  const [param, setPlanId] = useSearchParam("plan");
  const list: PlanSummary[] = plans.data ?? [];
  const fallback = list.find((p) => p.status === "published") ?? list[0] ?? null;
  const fromParam = param ? (list.find((p) => p.plan_id === param) ?? null) : null;
  // A just-created version may not be in the list yet; trust the param while the list refreshes.
  const planId = fromParam?.plan_id ?? (param && plans.isFetching ? param : (fallback?.plan_id ?? null));
  const summary = list.find((p) => p.plan_id === planId) ?? null;
  return { date, plans, list, planId, summary, setPlanId };
}
