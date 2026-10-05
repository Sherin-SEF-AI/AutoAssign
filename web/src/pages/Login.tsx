import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../state/auth";
import { useLogin } from "../state/queries";
import { ErrorBanner } from "../components/Toast";
import { btnPrimaryCls, inputCls } from "../components/ui";

function safeNext(next: string | null): string {
  // Only allow same-app relative paths.
  if (next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login")) return next;
  return "/trips";
}

export default function LoginPage() {
  const { token } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const next = safeNext(params.get("next"));

  if (token && !login.isPending) return <Navigate to={next} replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ email: email.trim(), password }, { onSuccess: () => navigate(next, { replace: true }) });
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-xs space-y-3 rounded border border-neutral-300 bg-white p-4 text-xs shadow-sm">
        <div>
          <div className="text-sm font-semibold">BluRabbit Dispatch</div>
          <div className="text-neutral-500">Sign in to continue</div>
        </div>
        <label className="block space-y-1">
          <span className="text-neutral-600">Email</span>
          <input
            type="email"
            autoComplete="username"
            required
            autoFocus
            className={`${inputCls} w-full py-1`}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="text-neutral-600">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            className={`${inputCls} w-full py-1`}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {login.error && <ErrorBanner error={login.error} />}
        <button type="submit" className={`${btnPrimaryCls} w-full py-1`} disabled={login.isPending}>
          {login.isPending ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
