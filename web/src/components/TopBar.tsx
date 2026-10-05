import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { clearAuth, useAuth } from "../state/auth";
import { useSearchParam, useServiceDate } from "../state/date";
import { useBudget, usePlans, type Budget } from "../state/queries";
import { useSseStatus } from "../state/sse";
import { addDays, todayIST, tomorrowIST } from "../lib/time";
import { btnCls, inputCls } from "./ui";

export const NAV = [
  { to: "/plan", label: "Plan" },
  { to: "/live", label: "Live" },
  { to: "/trips", label: "Trips" },
  { to: "/fleet", label: "Fleet" },
  { to: "/data", label: "Data" },
  { to: "/settings", label: "Settings" },
  { to: "/jobs", label: "Jobs" },
] as const;

function PlanSelector({ date }: { date: string }) {
  const plans = usePlans(date);
  const [plan, setPlan] = useSearchParam("plan");
  const list = plans.data ?? [];
  const disabled = plans.isError || list.length === 0;
  const selected = list.find((p) => p.plan_id === plan) ?? list[0];
  return (
    <>
      <select
        aria-label="Plan version"
        className={`${inputCls} w-36`}
        disabled={disabled}
        value={disabled ? "" : (selected?.plan_id ?? "")}
        onChange={(e) => setPlan(e.target.value)}
      >
        {disabled ? (
          <option value="">{plans.isLoading ? "Loading plans" : "No plans"}</option>
        ) : (
          list.map((p) => (
            <option key={p.plan_id} value={p.plan_id}>
              v{p.version} ({p.status})
            </option>
          ))
        )}
      </select>
      <PublishChip status={disabled ? null : (selected?.status ?? null)} />
    </>
  );
}

function PublishChip({ status }: { status: string | null }) {
  const cls =
    status === "published"
      ? "border-emerald-300 bg-emerald-50 text-emerald-800"
      : status
        ? "border-amber-300 bg-amber-50 text-amber-800"
        : "border-neutral-300 bg-neutral-50 text-neutral-500";
  return (
    <span className={`whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] ${cls}`} title="Publish state">
      {status ?? "unpublished"}
    </span>
  );
}

function budgetText(b: Budget): string | null {
  const pct = typeof b.pct === "number" ? b.pct : typeof b.used === "number" && typeof b.limit === "number" && b.limit > 0 ? (b.used / b.limit) * 100 : null;
  if (pct === null) return null;
  return `${b.provider ? `${b.provider} ` : ""}${Math.round(pct)}%`;
}

function BudgetIndicator() {
  const { isAdmin } = useAuth();
  const q = useBudget(isAdmin);
  if (!isAdmin || !q.data) return null;
  const items = Array.isArray(q.data) ? q.data : [q.data];
  const texts = items.map(budgetText).filter((t): t is string => !!t);
  if (texts.length === 0) return null;
  return (
    <span className="whitespace-nowrap rounded border border-neutral-300 bg-white px-1.5 py-0.5 text-[11px] text-neutral-600" title="Provider budget used">
      Budget {texts.join(", ")}
    </span>
  );
}

function LiveDot() {
  const s = useSseStatus();
  const cls = s === "open" ? "bg-emerald-500" : s === "idle" ? "bg-neutral-300" : "bg-neutral-400";
  return <span title={`Event stream: ${s}`} className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}

export function TopBar() {
  const { email, role } = useAuth();
  const [date, setDate] = useServiceDate();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const location = useLocation();
  const today = todayIST();
  const tomorrow = tomorrowIST();

  const logout = () => {
    clearAuth();
    qc.clear();
    navigate("/login", { replace: true });
  };

  // Carry only the shareable date param across pages.
  const navSearch = `?date=${date}`;

  return (
    <header className="border-b border-neutral-300 bg-white">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-xs">
        <div className="flex items-center gap-1.5 whitespace-nowrap text-sm font-semibold">
          <LiveDot />
          BluRabbit Dispatch
        </div>
        <div className="flex items-center gap-1">
          <button type="button" className={btnCls} onClick={() => setDate(addDays(date, -1))} aria-label="Previous day">
            &lt;
          </button>
          <input type="date" aria-label="Service date" className={inputCls} value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
          <button type="button" className={btnCls} onClick={() => setDate(addDays(date, 1))} aria-label="Next day">
            &gt;
          </button>
          <span className="whitespace-nowrap text-[11px] text-neutral-500">{date === today ? "today" : date === tomorrow ? "tomorrow" : ""}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <PlanSelector date={date} />
        </div>
        <BudgetIndicator />
        <div className="ml-auto flex items-center gap-2">
          <span className="max-w-[200px] truncate text-neutral-600" title={email ?? ""}>
            {email}
          </span>
          <span className="rounded bg-neutral-100 px-1 text-[10px] uppercase text-neutral-600">{role}</span>
          <button type="button" className={btnCls} onClick={logout}>
            Logout
          </button>
        </div>
      </div>
      <nav className="flex gap-0.5 overflow-x-auto border-t border-neutral-200 bg-neutral-50 px-2 text-xs">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={{ pathname: n.to, search: navSearch }}
            className={({ isActive }) =>
              `whitespace-nowrap border-b-2 px-3 py-1.5 ${isActive ? "border-neutral-800 font-semibold text-neutral-900" : "border-transparent text-neutral-600 hover:text-neutral-900"}`
            }
            aria-current={location.pathname === n.to ? "page" : undefined}
          >
            {n.label}
          </NavLink>
        ))}
      </nav>
    </header>
  );
}
