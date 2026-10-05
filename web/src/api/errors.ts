// RFC 7807 problem+json handling. Every non-2xx response becomes an ApiError.

export interface FieldError {
  field: string;
  message: string;
}

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  instance?: string;
  errors?: FieldError[];
}

export class ApiError extends Error {
  readonly code: string;
  readonly detail: string;
  readonly status: number;
  readonly errors: FieldError[];

  constructor(code: string, detail: string, status: number, errors: FieldError[] = []) {
    super(`${code}: ${detail}`);
    this.name = "ApiError";
    this.code = code;
    this.detail = detail;
    this.status = status;
    this.errors = errors;
  }

  /** Message for a given form field, matching "soc_pct" or a dotted suffix like "body.soc_pct". */
  fieldError(field: string): string | undefined {
    return this.errors.find((e) => e.field === field || e.field.endsWith(`.${field}`))?.message;
  }
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function parseFieldErrors(v: unknown): FieldError[] {
  if (!Array.isArray(v)) return [];
  const out: FieldError[] = [];
  for (const e of v) {
    if (!isObj(e)) continue;
    // Problem shape: {field, message}
    if (typeof e.field === "string" || typeof e.message === "string") {
      out.push({ field: String(e.field ?? ""), message: String(e.message ?? "") });
      continue;
    }
    // FastAPI default shape: {loc, msg}
    if (Array.isArray(e.loc) || typeof e.msg === "string") {
      const loc = Array.isArray(e.loc) ? e.loc.filter((p) => p !== "body").join(".") : "";
      out.push({ field: loc, message: String(e.msg ?? "") });
    }
  }
  return out;
}

/** Build an ApiError from a status code and whatever body came back (parsed JSON, text or nothing). */
export function toApiError(status: number, body: unknown, statusText = ""): ApiError {
  if (body instanceof ApiError) return body;
  if (typeof body === "string") {
    const text = body;
    try {
      body = JSON.parse(text);
    } catch {
      return new ApiError(codeForStatus(status), text || statusText || "request failed", status);
    }
  }
  if (isObj(body)) {
    const p = body as Problem & { detail?: unknown };
    const errors = parseFieldErrors(p.errors ?? (Array.isArray(p.detail) ? p.detail : undefined));
    const code = typeof p.code === "string" && p.code ? p.code : codeForStatus(status);
    let detail: string;
    if (typeof p.detail === "string") detail = p.detail;
    else if (typeof p.title === "string") detail = p.title;
    else if (errors.length) detail = "request validation failed";
    else detail = statusText || "request failed";
    return new ApiError(code, detail, typeof p.status === "number" ? p.status : status, errors);
  }
  return new ApiError(codeForStatus(status), statusText || "request failed", status);
}

export function codeForStatus(status: number): string {
  if (status === 0) return "network_error";
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "validation_error";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server_error";
  return "http_error";
}

/** Normalise any thrown value into an ApiError for display. */
export function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  if (e instanceof Error) return new ApiError("client_error", e.message, 0);
  return new ApiError("client_error", String(e), 0);
}
