/** The one place this app talks to the server. */
import type { components } from "./api-types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    /** The server's message, written for the person at the form. Shown verbatim. */
    readonly detail: string,
    /** Some endpoints answer with a structured detail — `/onboarding/complete` lists what is missing. */
    readonly data?: unknown,
  ) {
    super(detail);
  }
}

/** Set by `request` when a refresh is in flight, so concurrent 401s wait rather than race. */
let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  refreshing ??= fetch("/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((response) => {
      // Read to its end, though nothing in it is used: an answer left unread keeps its request open, and a
      // browser never counted the page as done loading.
      void response.text().catch(() => undefined);
      // Only the server's own "no" is signed out. A server that is down, or no connection at all, says nothing
      // about the session: read as signed out, either one showed the landing page to somebody signed in and
      // threw away the marks kept for exactly a lost connection (`marks.ts`).
      if (!response.ok && response.status !== 401) throw new ApiError(response.status, response.statusText);
      return response.ok;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

type Options = {
  method?: string;
  body?: unknown;
  /** Multipart, for document upload. Passed straight through — the browser sets the boundary. */
  form?: FormData;
  /** Internal: stops a refreshed request from refreshing again and looping forever. */
  retry?: boolean;
  /** Sent through even if the page closes meanwhile: what a page saves as it is left. */
  keepalive?: boolean;
};

export async function request<T>(path: string, options: Options = {}): Promise<T> {
  const { method = "GET", body, form, retry = false, keepalive } = options;

  const response = await fetch(path, {
    method,
    keepalive,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
  });

  if (response.status === 204) return undefined as T;

  // Read before anything else is decided: an answer left unread, as a 401's was when the request was asked
  // again, keeps its request open.
  const payload = await response.json().catch(() => null);
  if (response.status === 401 && !retry && !path.startsWith("/auth/")) {
    // An expired 15-minute access token is the ordinary case, not an error worth showing anybody.
    if (await refresh()) return request<T>(path, { ...options, retry: true });
  }

  if (!response.ok) {
    const detail = payload?.detail;
    throw new ApiError(
      response.status,
      // FastAPI's validation errors arrive as a list of objects.
      typeof detail === "string" ? detail : detailMessage(detail) ?? unsaid(response),
      detail,
    );
  }
  return payload as T;
}

/** What a refusal says when it says nothing itself, such as an answer that is not the server's own. It said
 * the status's words, which over HTTP/2 and 3, as browsers reach the site through Cloudflare, are none: a form
 * showed nothing and its button stopped (2026-11-22). Cloudflare marks a check it wants the browser to pass
 * (`cf-mitigated: challenge`), which a page load can pass and a request from the page cannot. */
function unsaid(response: Response): string {
  return response.headers.get("cf-mitigated") === "challenge"
    ? "The site needs to check this browser first. Reload the page, then try again."
    : `Something went wrong (error ${response.status}). Reload the page and try again.`;
}

function detailMessage(detail: unknown): string | undefined {
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  // What is wrong with one field, as the password rules say it (`{"password": ["is too common"]}`): it said
  // nothing at all, and people whose password was refused saw the sign-up button stop (2026-11-22).
  const [field, problems] = detail && typeof detail === "object" && !Array.isArray(detail)
    ? Object.entries(detail)[0] ?? [] : [];
  if (field && Array.isArray(problems) && problems.length) {
    return `${field.charAt(0).toUpperCase()}${field.slice(1)} ${problems.join(" and ")}.`;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : undefined;
    return field ? `${String(field)}: ${first.msg ?? "is not valid"}` : first.msg;
  }
  return undefined;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  put: <T,>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  patch: <T,>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  del: <T,>(path: string, body?: unknown) => request<T>(path, { method: "DELETE", body }),
  upload: <T,>(path: string, form: FormData) => request<T>(path, { method: "POST", form }),
};

/* ── the shapes this app reads ────────────────────────────────────────────────────────────────── */

/** Every response is the server's own model, generated (`npm run types`): a field it stops sending
 * fails the build, not the page. `Schema<"FeedJob">` is `components["schemas"]["FeedJob"]`. */
export type Schema<K extends keyof components["schemas"]> = components["schemas"][K];

export type Dashboard = Schema<"PlanDashboard"> | Schema<"OnboardingDashboard"> | Schema<"ReadyDashboard">;
export type Membership = Schema<"Membership">;
export type Plan = Schema<"Plan">;
export type Ready = Schema<"ReadyDashboard">;
/** What the word screens edit. The review sends it as a plain object, so it is read as the very
 * model the save is checked against. */
export type Config = Schema<"UserConfigSchema">;
