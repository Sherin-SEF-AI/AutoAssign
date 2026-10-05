import createClient, { type Middleware } from "openapi-fetch";
import type { paths, components } from "./schema";
import { ApiError, toApiError } from "./errors";
import { clearAuth, getToken } from "../state/auth";

export type Schemas = components["schemas"];

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

const LOGIN_PATH = "/api/v1/auth/login";

function handleUnauthorized(): void {
  clearAuth();
  if (window.location.pathname !== "/login") {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
  }
}

const authMiddleware: Middleware = {
  onRequest({ request }) {
    const token = getToken();
    if (token && !request.headers.has("Authorization")) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
  onResponse({ request, response }) {
    if (response.status === 401 && !new URL(request.url, window.location.origin).pathname.endsWith(LOGIN_PATH)) {
      handleUnauthorized();
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: API_BASE });
api.use(authMiddleware);

type FetchResult<T> = Promise<{ data?: T; error?: unknown; response: Response }>;

/** Await an openapi-fetch call and return data, throwing ApiError on any non-2xx or network failure. */
export async function unwrap<T>(p: FetchResult<T>): Promise<T> {
  let r: Awaited<FetchResult<T>>;
  try {
    r = await p;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("network_error", e instanceof Error ? e.message : "network request failed", 0);
  }
  if (!r.response.ok) throw toApiError(r.response.status, r.error, r.response.statusText);
  return r.data as T;
}

/** URL of the SSE stream, with the JWT in the query string (EventSource cannot set headers). */
export function eventStreamUrl(token: string): string {
  return `${API_BASE}/api/v1/events/stream?token=${encodeURIComponent(token)}`;
}
