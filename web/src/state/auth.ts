import { useSyncExternalStore } from "react";

export type Role = "ops" | "admin";

export interface AuthState {
  token: string | null;
  role: Role | null;
  email: string | null;
}

const KEY = "blurabbit.auth";

function safeRead(): AuthState {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return { token: null, role: null, email: null };
    const v = JSON.parse(raw) as Partial<AuthState>;
    return {
      token: typeof v.token === "string" ? v.token : null,
      role: v.role === "admin" || v.role === "ops" ? v.role : null,
      email: typeof v.email === "string" ? v.email : null,
    };
  } catch {
    return { token: null, role: null, email: null };
  }
}

function safeWrite(s: AuthState): void {
  try {
    if (s.token) window.localStorage.setItem(KEY, JSON.stringify(s));
    else window.localStorage.removeItem(KEY);
  } catch {
    // storage blocked: keep in memory only
  }
}

let state: AuthState = safeRead();
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export function getAuth(): AuthState {
  return state;
}

export function getToken(): string | null {
  return state.token;
}

export function setAuth(token: string, role: string, email: string): void {
  state = { token, role: role === "admin" ? "admin" : "ops", email };
  safeWrite(state);
  emit();
}

export function clearAuth(): void {
  state = { token: null, role: null, email: null };
  safeWrite(state);
  emit();
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

// Keep tabs in sync.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key === KEY) {
      state = safeRead();
      emit();
    }
  });
}

export function useAuth(): AuthState & { isAdmin: boolean } {
  const s = useSyncExternalStore(subscribe, getAuth, getAuth);
  return { ...s, isAdmin: s.role === "admin" };
}
