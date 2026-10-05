import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { clearAuth, useAuth } from "../state/auth";
import { useServiceDate } from "../state/date";
import { useBudget } from "../state/queries";
import { useSelectedPlan } from "../state/plan";
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

function PlanSelector() {
  const { plans, list, planId, summary, setPlanId } = useSelectedPlan();
  const disabled = plans.isError || list.length === 0;
  return (
    <>
      <select
        aria-label="Plan version"
        className={`${inputCls} w-44`}
        disabled={disabled}
        value={disabled ? "" : (planId ?? "")}
        onChange={(e) => setPlanId(e.target.value)}
      >
        {disabled ? (
          <option value="">{plans.isLoading ? "Loading plans" : "No plans"}</option>
        ) : (
          list.map((p) => (
            <option key={p.plan_id} value={p.plan_id}>
              v{p.version} {p.status} ({p.trigger})
            </option>
          ))
        )}
      </select>
      <PublishChip status={disabled ? null : (summary?.status ?? null)} />
    </>
  );
}

function PublishChip({ status }: { status: string | null }) {
  const cls =
    status === "published"
      ? "border-emerald-300 bg-emerald-50 text-emerald-800"
      : status === "draft"
        ? "border-amber-300 bg-amber-50 text-amber-800"
        : "border-neutral-300 bg-neutral-50 text-neutral-500";
  return (
    <span className={`whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] ${cls}`} title="Publish state">
      {status ?? "no plan"}
    </span>
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  here_routing: "route",
  here_matrix: "matrix",
  here_matrix_elements: "elems",
};

function BudgetIndicator() {
  const q = useBudget();
  if (!q.data) return null;
  const b = q.data;
  return (
    <div className="flex flex-wrap items-center gap-1.5" title={`Provider budget for ${b.day}`}>
      {!b.here_enabled && <span className="whitespace-nowrap rounded border border-amber-300 bg-amber-50 px-1 py-px text-[10px] font-medium text-amber-800">HERE off</span>}
      {!b.osrm_enabled && <span className="whitespace-nowrap rounded border border-amber-300 bg-amber-50 px-1 py-px text-[10px] font-medium text-amber-800">OSRM off</span>}
      {b.providers.map((p) => {
        const bad = p.remaining <= 0 || p.circuit === "open";
        const warn = !bad && (p.circuit === "half_open" || (p.cap > 0 && p.used / p.cap >= 0.8));
        const pct = p.cap > 0 ? Math.min(100, (p.used / p.cap) * 100) : 100;
        const color = bad ? "bg-red-500" : warn ? "bg-amber-500" : "bg-emerald-500";
        return (
          <span
            key={p.provider}
            className={`inline-flex items-center gap-1 whitespace-nowrap text-[10px] ${bad ? "text-red-700" : "text-neutral-600"}`}
            title={`${p.provider}: ${p.used} / ${p.cap} used, ${p.remaining} remaining, circuit ${p.circuit}`}
          >
            {PROVIDER_LABEL[p.provider] ?? p.provider}
            <span className="inline-block h-1.5 w-10 overflow-hidden rounded bg-neutral-200">
              <span className={`block h-full ${color}`} style={{ width: `${pct}%` }} />
            </span>
            <span className="font-mono">
              {p.used}/{p.cap}
            </span>
            {p.circuit !== "closed" && <span className={`rounded px-0.5 ${p.circuit === "open" ? "bg-red-100" : "bg-amber-100"}`}>{p.circuit}</span>}
          </span>
        );
      })}
    </div>
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
          <PlanSelector />
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
