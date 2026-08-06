/** The one place this app talks to the server. */

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
    .then((response) => response.ok)
    .catch(() => false)
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
};

export async function request<T>(path: string, options: Options = {}): Promise<T> {
  const { method = "GET", body, form, retry = false } = options;

  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
  });

  if (response.status === 401 && !retry && !path.startsWith("/auth/")) {
    // An expired 15-minute access token is the ordinary case, not an error worth showing anybody.
    if (await refresh()) return request<T>(path, { ...options, retry: true });
  }

  if (response.status === 204) return undefined as T;

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new ApiError(
      response.status,
      // FastAPI's validation errors arrive as a list of objects.
      typeof detail === "string" ? detail : detailMessage(detail) ?? response.statusText,
      detail,
    );
  }
  return payload as T;
}

function detailMessage(detail: unknown): string | undefined {
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
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

export type OnboardingPage = {
  key: string;
  title: string;
  complete: boolean;
  detail: string;
};

export type OnboardingState = {
  complete: boolean;
  next_page: string | null;
  completed_at: string | null;
  pages: OnboardingPage[];
};

export type CredentialList = {
  credentials: { kind: string; last_4: string | null; validated: boolean }[];
  requirements: { claude: boolean; telegram_bot: boolean; proxy: boolean };
};

export type DocumentList = {
  documents: { id: number; kind: string; filename: string; chars: number; preview: string }[];
  supported: string[];
  limit: number;
};

export type ConfigPreview = {
  matches: number;
  window_days: number;
  examples: string[];
  detail: string;
};

export type ProcessState = {
  action: "confirm" | "process" | "upload" | "ready";
  message: string;
  documents: number;
  has_draft: boolean;
  profile_generation: number;
  warning: {
    replaces_profile: boolean;
    labels_invalidated: number;
    relabel_cost_estimate_usd: number;
    text: string;
  };
};

export type Dashboard =
  | { stage: "onboarding"; onboarding: OnboardingState }
  | {
      stage: "personal" | "rules";
      next: ProcessState;
      model: {
        version: number;
        servable: boolean;
        stale_reason: string | null;
        trained_at: string | null;
        /** Every version this account has had, newest first. */
        history: {
          version: number;
          active: boolean;
          n_labels: number | null;
          trained_at: string | null;
          metrics: { key: string; label: string; value: string | number }[];
        }[];
      };
      /** Whether setup is finished, by the same check the trainer refuses on. */
      may_train: boolean;
      feed: { scored: number; labels: number; applied: number };
      documents: number;
      telegram: string;
      notifications: { per_day_cap: number; interval_min: number };
      banners: { level: "error" | "warning" | "info"; key: string; text: string }[];
      // Whether to render the operator panel. A hint, not the access control.
      is_admin?: boolean;
    };

export type Salary = {
  min: number | null;
  max: number | null;
  currency: string;
  period: string;
  raw?: string;
} | null;

export type FeedJob = {
  id: number;
  title: string;
  company: string | null;
  location: string | null;
  is_remote: boolean | null;
  url: string;
  source: string;
  score: number | null;
  posted_at: string | null;
  first_seen_at: string | null;
  salary: Salary;
  applied_at: string | null;
};

export type Feed = {
  stage: "personal" | "rules";
  approximate: boolean;
  detail?: string;
  total: number;
  sources: string[];
  jobs: FeedJob[];
};

export type Consent = {
  granted: boolean;
  current_version: string;
  disclosure: {
    version: string;
    headline: string;
    body: string;
    processors: {
      name: string;
      purpose: string;
      data: string;
      on_your_credential: boolean;
      location: string;
    }[];
  };
};

export type Account = {
  email: string;
  full_name: string | null;
  links: { url: string; platform: string }[];
  timezone: string;
  active: boolean;
  notify_interval_min: number;
  telegram_status: string;
  plan: string;
  consent_version: string | null;
};
