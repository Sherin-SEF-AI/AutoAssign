import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { TopBar } from "./TopBar";
import { SimBanner } from "./SimBanner";
import { useAuth } from "../state/auth";
import { useEventStream } from "../state/sse";
import { useMe } from "../state/queries";

/** Authenticated shell: fixed top bar, sim banner, one SSE subscription. */
export function Layout() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const location = useLocation();
  useEventStream(qc, token);
  // Validates the stored token on load; a 401 clears it and redirects via the client middleware.
  useMe(!!token);

  if (!token) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }
  return (
    <div className="min-h-screen">
      {/* Header stays pinned to the top (sticky, so no spacer math when it wraps on narrow screens). */}
      <div className="sticky top-0 z-40">
        <TopBar />
        <SimBanner />
      </div>
      <main className="mx-auto max-w-[1920px] p-3">
        <Outlet />
      </main>
    </div>
  );
}
