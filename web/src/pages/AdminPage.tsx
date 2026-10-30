/** The operator panel — what needs a person, and who the accounts are. */
import { Fragment, useEffect, useRef, useState } from "react";

import { api } from "../api";
import { Error, useAction } from "../ui";

type Alert = {
  severity: "page" | "warn";
  key: string;
  text: string;
  action: string;
  user_email: string | null;
};

type Lane = {
  lane: string;
  ready: number;
  claimed: number;
  dead: number;
  oldest_ready_min: number | null;
};

type Health = {
  checked_at: string;
  lanes: Lane[];
  users_total: number;
  users_active: number;
  corpus_rows: number;
  database_bytes: number;
  database_budget_bytes: number;
  connections: number;
  alerts: Alert[];
  latency: {
    window_hours: number;
    found_to_delivered: Span;
  };
};

type AdminUser = {
  id: string;
  email: string;
  status: string;
  active: boolean;
  telegram: string;
  onboarded: boolean;
  is_admin: boolean;
  /** Last sign-in or Telegram tap. Null for an account that has never done either. */
  last_active_at: string | null;
};

/** One latency stage. */
type Span = {
  p50: number | null;
  p90: number | null;
  p99: number | null;
  samples: number;
  verdict?: "ok" | "warn" | "down" | "unknown";
  amber_after_min?: number;
  red_after_min?: number;
};

/** One zone of the pipeline, as the table renders it collapsed. */
type StageRow = {
  key: string;
  label: string;
  zone: string;
  what: string;
  scope: "corpus" | "user";
  caveat: string | null;
  p50: number | null;
  p90: number | null;
  p99: number | null;
  worst: number | null;
  samples: number;
  verdict: "ok" | "warn" | "down" | "unknown" | "unjudged";
  judged: boolean;
  amber_after_min: number | null;
  red_after_min: number | null;
};

type SeriesPoint = { at: string; samples: number; p50: number | null; p90: number | null };

/** What one expanded row shows. */
type StageDetail = {
  key: string;
  label: string;
  zone: string;
  what: string;
  caveat: string | null;
  window_hours: number;
  lane_name: string | null;
  // Seconds, from the queue's own timestamps.
  lane: {
    lane: string;
    samples: number;
    wait_p50: number;
    wait_p90: number;
    run_p50: number;
    run_p90: number;
    run_max: number;
  } | null;
  backlog: { waiting: number; oldest_min: number | null } | null;
  series: SeriesPoint[];
  reasoning: string | null;
  amber_after_min: number | null;
  red_after_min: number | null;
};

type LatencyReport = { window_hours: number; stages: StageRow[] };

/** One stage as it stands right now. */
type PipelineStage = {
  key: string;
  label: string;
  what: string;
  entered: number | null;
  // `null`, not 0, where the stage has no queue at all.
  waiting: number | null;
  oldest_min: number | null;
  verdict: "ok" | "warn" | "down";
};

type PipelineLane = {
  lane: string;
  // Waiting for a worker.
  ready: number;
  // Claimed and executing — the number that moves second to second.
  running: number;
  // Abandoned.
  dead: number;
  done: number;
};

/** One lane's work for one account. `lanes` says "docgen 2 dead"; this says whose, and why. */
type LaneAccount = {
  lane: string;
  user_id: string;
  email: string;
  ready: number;
  running: number;
  dead: number;
  done: number;
  last_error: string | null;
};

type PipelineReport = {
  window_hours: number;
  stages: PipelineStage[];
  lanes: PipelineLane[];
  lane_accounts: LaneAccount[];
  at: string;
  amber_after_min: number;
  red_after_min: number;
};

type Settings = {
  job_max_age_hours: number;
  source_freshness_hours: Record<string, number>;
  source_min_age_hours: Record<string, number>;
  default_freshness_hours: number;
  source_queries_per_cycle: Record<string, number>;
  source_results_per_query: Record<string, number>;
  source_pages_per_cycle: Record<string, number>;
  ats_companies_per_cycle: number;
  describe_per_cycle: number;
  linkedin_concurrent_users: number;
  /** Per provider: `anthropic`, `gemini`, `openai`. */
  document_models: Record<string, string>;
  document_keep_hours: number;
  /** The help chat: 0 off, 1 on. */
  support_chat_enabled: number;
  corpus_retention_days: number;
  database_budget_mb: number;
};

type Source = {
  name: string;
  hours: number;
  /** How long a posting from this source stays deliverable once stored — the floor the global
   *  window cannot push below. The other half of `hours`, and until 2026-08-09 it had no control. */
  min_age_hours: number;
  queries_per_cycle: number;
  /** `null` where the source has no result cap to set — it serves a feed or counts pages. */
  results_per_query: number | null;
  /** `null` where the source fetches one response rather than walking pages. */
  pages_per_cycle: number | null;
  /** How many distinct queries this source currently has. 0 means it takes none. */
  wanted: number;
};

/** The ATS ceiling and the number it is a ceiling on. */
type AtsInfo = { companies_per_cycle: number; companies_held: number };


/** The four questions an operator arrives with, in the order they ask them. */
const TABS: [Tab, string][] = [
  ["health", "Health"],
  ["settings", "Settings"],
  ["corpus", "Corpus"],
  ["pipeline", "Pipeline"],
  ["latency", "Latency"],
  ["accounts", "Accounts"],
];

type Tab = "health" | "settings" | "corpus" | "pipeline" | "latency" | "accounts";

type CorpusJob = {
  id: number;
  title: string;
  company: string | null;
  location: string | null;
  source: string | null;
  is_remote: boolean;
  described: boolean;
  url: string;
  first_seen_at: string | null;
};

type CorpusPage = {
  total: number;
  offset: number;
  page_size: number;
  sources: { source: string; count: number }[];
  jobs: CorpusJob[];
};

type Keywords = {
  operator: { id: number; keyword: string; location: string | null; note: string | null }[];
  users: { keyword: string; accounts: string[]; deletable: boolean }[];
};

function BinIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 7h16" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M9 7V5.5A1.5 1.5 0 0110.5 4h3A1.5 1.5 0 0115 5.5V7"
            stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="M6 7l.8 11.2A2 2 0 008.8 20h6.4a2 2 0 002-1.8L18 7"
            stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * How long since an account did anything, in the unit an operator judges on.
 *
 * A date makes the reader do the subtraction; "45 days" is the answer they arrived for. This is the
 * whole of account lifecycle now — there is no automatic dormancy, so the only consequence of a
 * large number here is a person deciding to delete the account.
 */
function lastActive(iso: string | null): string {
  if (!iso) return "never";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days < 1) return "today";
  return days === 1 ? "1 day" : `${days} days`;
}

export function AdminPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [ats, setAts] = useState<AtsInfo | null>(null);
  // The account being deleted, held until the typed address matches. `null` closes the dialog.
  const [doomed, setDoomed] = useState<AdminUser | null>(null);
  const [typed, setTyped] = useState("");
  const [tab, setTab] = useState<Tab>("health");
  const [failed, setFailed] = useState(false);

  // One source, one number.
  const savePlatform = useAction(async (key: string, value: number) => {
    const body = await api.put<{ settings: Settings }>(`/admin/settings/${key}`, { value });
    setSettings(body.settings);
  });

  const saveResults = useAction(async (name: string, value: number) => {
    const next = { ...(settings?.source_results_per_query ?? {}), [name]: value };
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/source_results_per_query", { value: next });
    setSettings(body.settings);
    setSources((rows) =>
      rows?.map((row) => (row.name === name ? { ...row, results_per_query: value } : row)) ?? rows);
  });

  const saveAtsCompanies = useAction(async (value: number) => {
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/ats_companies_per_cycle", { value });
    setSettings(body.settings);
    setAts((row) => (row ? { ...row, companies_per_cycle: value } : row));
  });

  const saveQueries = useAction(async (name: string, value: number) => {
    const next = { ...(settings?.source_queries_per_cycle ?? {}), [name]: value };
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/source_queries_per_cycle", { value: next });
    setSettings(body.settings);
    setSources((rows) =>
      rows?.map((row) => (row.name === name ? { ...row, queries_per_cycle: value } : row)) ?? rows);
  });

  const savePages = useAction(async (name: string, value: number) => {
    const next = { ...(settings?.source_pages_per_cycle ?? {}), [name]: value };
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/source_pages_per_cycle", { value: next });
    setSettings(body.settings);
    setSources((rows) =>
      rows?.map((row) => (row.name === name ? { ...row, pages_per_cycle: value } : row)) ?? rows);
  });

  const saveFreshness = useAction(async (name: string, hours: number) => {
    const next = { ...(settings?.source_freshness_hours ?? {}), [name]: hours };
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/source_freshness_hours", { value: next });
    setSettings(body.settings);
    setSources((rows) => rows?.map((row) => (row.name === name ? { ...row, hours } : row)) ?? rows);
  });

  // The other half of the same question, and it had no control until 2026-08-09: `fresh` is how
  // far back the source is asked, `keep` is how long what it returned stays deliverable. Setting
  // the first without the second meant an operator could widen a window, watch rows arrive, and
  // never learn that a floor they could not see was discarding them.
  const saveMinAge = useAction(async (name: string, hours: number) => {
    const next = { ...(settings?.source_min_age_hours ?? {}), [name]: hours };
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/source_min_age_hours", { value: next });
    setSettings(body.settings);
    setSources((rows) =>
      rows?.map((row) => (row.name === name ? { ...row, min_age_hours: hours } : row)) ?? rows);
  });

  // The alert key is `dead_letter:<lane>`.
  const discardDead = useAction(async (lane: string) => {
    await api.del(`/admin/queue/dead?lane=${encodeURIComponent(lane)}`);
    const fresh = await api.get<Health>("/admin/health");
    setHealth(fresh);
  });

  const toggleAdmin = useAction(async (user: AdminUser) => {
    await api.put(`/admin/users/${user.id}/admin`, { is_admin: !user.is_admin });
    const fresh = await api.get<{ users: AdminUser[] }>("/admin/users");
    setUsers(fresh.users);
  });

  const removeUser = useAction(async (user: AdminUser, email: string) => {
    await api.del(`/admin/users/${user.id}`, { email });
    const fresh = await api.get<{ users: AdminUser[] }>("/admin/users");
    setUsers(fresh.users);
    setDoomed(null);
    setTyped("");
  });

  // One provider's model, saved when the field is left.
  const saveModel = useAction(async (provider: string, model: string) => {
    if (!model || model === settings?.document_models[provider]) return;
    const next = { ...(settings?.document_models ?? {}), [provider]: model };
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/document_models", { value: next });
    setSettings(body.settings);
  });

  const saveMaxAge = useAction(async (hours: number) => {
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/job_max_age_hours", { value: hours });
    setSettings(body.settings);
  });

  // The account opens as its own page, at its own address.
  const openUser = useAction(async (id: string) => {
    window.location.hash = `/admin/users/${id}`;
  });

  useEffect(() => {
    Promise.all([
      api.get<Health>("/admin/health"),
      api.get<{ users: AdminUser[] }>("/admin/users"),
      api.get<{ settings: Settings }>("/admin/settings"),
      api.get<{ sources: Source[]; ats: AtsInfo }>("/admin/sources"),
    ])
      .then(([h, u, s, src]) => {
        setHealth(h);
        setUsers(u.users);
        setSettings(s.settings);
        setSources(src.sources);
        setAts(src.ats);
      })
      .catch(() => setFailed(true));
  }, []);

  if (failed) return <p className="error">Could not read the platform state.</p>;
  if (!health || !users) return <p className="muted">Loading…</p>;

  return (
    <>
      <p className="kicker">Operator</p>
      <div className="spread">
        <h1>Platform</h1>
        <span className="small muted">
          checked {new Date(health.checked_at).toLocaleTimeString()}
        </span>
      </div>

      {/* Tabs, because this page had six stacked sections and no shape. */}
      <nav className="tabs">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={tab === key ? "tab on" : "tab"}
            onClick={() => setTab(key)}
          >
            {label}
            {key === "health" && health.alerts.length > 0 ? (
              <span className="tab-count">{health.alerts.length}</span>
            ) : null}
          </button>
        ))}
      </nav>

      {tab === "health" ? (
        <>
      {/* What needs a person. */}
      {health.alerts.length === 0 ? (
        <p className="muted">Nothing needs a person right now.</p>
      ) : (
        <div style={{ marginBottom: "2rem" }}>
          {health.alerts.map((alert) => (
            <div key={alert.key} className={`banner ${alert.severity === "page" ? "error" : "warning"}`}
                 style={{ marginBottom: "0.6rem" }}>
              {/* Only the dead-letter alert is dismissable. */}
              {alert.key.startsWith("dead_letter") ? (
                <button className="alert-dismiss" disabled={discardDead.busy}
                        title="Discard these dead-lettered jobs. The failure is written to the log first."
                        onClick={() => void discardDead.run(alert.key.split(":")[1] ?? "")}>
                  ×
                </button>
              ) : null}
              <strong>{alert.severity === "page" ? "Needs you now" : "Needs you today"}</strong>
              {alert.user_email ? <span className="muted"> · {alert.user_email}</span> : null}
              <br />
              {alert.text}
              <br />
              <span className="small muted">{alert.action}</span>
            </div>
          ))}
          <Error>{discardDead.error}</Error>
        </div>
      )}

      <dl className="stats">
        <Stat value={health.corpus_rows.toLocaleString()} label="corpus rows" />
        <Stat value={`${health.users_active}/${health.users_total}`} label="active users" />
        <Stat
          value={`${Math.round(health.database_bytes / 1024 ** 2)} of ${Math.round(health.database_budget_bytes / 1024 ** 2)} MB`}
          label="database"
        />
      </dl>

          <p className="kicker" style={{ marginTop: "2.5rem" }}>Queue</p>
      <table>
        <thead>
          <tr>
            <th>Lane</th>
            <th>Ready</th>
            <th>Claimed</th>
            <th>Dead</th>
            <th>Oldest</th>
          </tr>
        </thead>
        <tbody>
          {health.lanes.map((lane) => (
            <tr key={lane.lane}>
              <td>{lane.lane}</td>
              <td className="num">{lane.ready}</td>
              <td className="num">{lane.claimed}</td>
              {/* Dead letters are the only number here worth a colour: everything else is a reading. */}
              <td className="num" style={lane.dead ? { color: "var(--danger)" } : undefined}>
                {lane.dead}
              </td>
              <td className="num job-sub">
                {lane.oldest_ready_min ? `${Math.round(lane.oldest_ready_min)}m` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
        </>
      ) : null}

      {tab === "settings" ? <>      {/* **What the operator owns.** Freshness and cadence stopped being user preferences on
          2026-08-01: the product streams the newest postings, so both are properties of what it is
          rather than choices. Per source, because sites differ in how fast they list. */}
      {settings ? (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ marginTop: 0 }}>Platform settings</h2>


          <label className="label" htmlFor="maxage" style={{ marginTop: "1.5rem" }}>
            Job shelf life
          </label>
          <span className="hint">
            How old a posting may be and still be scored and sent. The only age check between the
            corpus and a feed — shorter means fresher cards and a thinner feed, longer means a
            posting keeps its chance to be sent while the queue drains.
          </span>
          <select
            id="maxage"
            value={settings.job_max_age_hours}
            disabled={saveMaxAge.busy}
            onChange={(event) => void saveMaxAge.run(Number(event.target.value))}
            style={{ maxWidth: "18rem" }}
          >
            {[6, 12, 24, 36, 48, 72, 96, 168].map((hours) => (
              <option key={hours} value={hours}>
                {hours < 48 ? `${hours} hours` : `${hours / 24} days`}
              </option>
            ))}
          </select>
          <Error>{saveMaxAge.error}</Error>

          <p className="label" style={{ marginTop: "1.5rem" }}>Per source</p>
          <span className="hint">
            <strong>Keep</strong> is how long a posting from that source stays deliverable once
            it is in the corpus, and it is the floor the global window cannot push below: at six
            hours globally, fifteen of seventeen sources returned nothing at all, because a board
            that publishes a few times a day has nothing posted in the last six.{" "}
            <strong>Freshness</strong> is how recent a posting must be to qualify. LinkedIn lists
            within minutes; a company board is reached whenever our token list gets there, so the
            same window would keep 2% of it. <strong>Queries</strong> is how many times a cycle that
            source may be asked — per source, because rate limits are, and one shared number made
            unrelated boards queue behind each other. <strong>Wanted</strong> is how many it
            currently has: equal or under the ceiling means it is fully served each cycle.
          </span>
          <Error>{saveFreshness.error ?? saveMinAge.error ?? saveQueries.error}</Error>
          <div className="sources">
            <div className="source-row source-head">
              <span className="source-name">source</span>
              <span>fresh</span>
              <span>keep</span>
              <span>queries</span>
              <span>results</span>
              <span>pages</span>
              <span className="num">wanted</span>
            </div>
            {(sources ?? []).map((source) => (
              <div key={source.name} className="source-row">
                <span className="source-name">{source.name}</span>
                <select
                  value={source.hours}
                  disabled={saveFreshness.busy}
                  aria-label={`${source.name} freshness`}
                  onChange={(event) =>
                    void saveFreshness.run(source.name, Number(event.target.value))}
                >
                  {[1, 2, 4, 6, 12, 24, 48, 72, 168].map((hours) => (
                    <option key={hours} value={hours}>{hours}h</option>
                  ))}
                </select>
                <select
                  value={source.min_age_hours}
                  disabled={saveMinAge.busy}
                  aria-label={`${source.name} keep`}
                  onChange={(event) =>
                    void saveMinAge.run(source.name, Number(event.target.value))}
                >
                  {[1, 2, 4, 6, 12, 24, 48, 72, 168].map((hours) => (
                    <option key={hours} value={hours}>{hours}h</option>
                  ))}
                </select>
                {/* Only the sources that take a query get a ceiling. */}
                {source.wanted > 0 ? (
                  <>
                    <select
                      value={source.queries_per_cycle}
                      disabled={saveQueries.busy}
                      aria-label={`${source.name} queries per cycle`}
                      onChange={(event) =>
                        void saveQueries.run(source.name, Number(event.target.value))}
                    >
                      {[5, 10, 20, 40, 60, 100, 200].map((n) => (
                        <option key={n} value={n}>{n}</option>
                      ))}
                    </select>
                    <Results source={source} save={saveResults} />
                    <Pages source={source} save={savePages} />
                    {/* Amber when the ceiling is below demand. */}
                    <span className={source.wanted > source.queries_per_cycle
                      ? "num behind" : "num muted"}>
                      {source.wanted}
                    </span>
                  </>
                ) : (
                  <>
                    {/* A feed source issues exactly one request a cycle. */}
                    <select value={1} disabled aria-label={`${source.name} queries per cycle`}
                            title="This board serves a feed and takes no search term, so it is
                                   fetched once a cycle.">
                      <option value={1}>1</option>
                    </select>
                    <Results source={source} save={saveResults} />
                    <Pages source={source} save={savePages} />
                    <span className="num muted">—</span>
                  </>
                )}
              </div>
            ))}
          </div>

          {/* The ATS ceiling, with the number it is a ceiling on. */}
          {ats ? (
            <>
              <p className="label" style={{ marginTop: "1.5rem" }}>Company boards per cycle</p>
              <span className="hint">
                The ATS family polls one company board at a time. We hold{" "}
                <strong>{ats.companies_held.toLocaleString()}</strong> company tokens, collected
                automatically from apply links we have seen. A cycle that polls fewer than we hold
                reaches the rest on later cycles.
              </span>
              <div className="row" style={{ marginTop: "0.6rem", alignItems: "center" }}>
                <select
                  value={ats.companies_per_cycle}
                  disabled={saveAtsCompanies.busy}
                  aria-label="ATS company boards per cycle"
                  onChange={(event) => void saveAtsCompanies.run(Number(event.target.value))}
                  style={{ maxWidth: "10rem" }}
                >
                  {[100, 200, 400, 800, 1500, 3000].map((n) => (
                    <option key={n} value={n}>{n.toLocaleString()}</option>
                  ))}
                </select>
                <span className={ats.companies_held > ats.companies_per_cycle
                  ? "small behind" : "small muted"}>
                  of {ats.companies_held.toLocaleString()} held
                </span>
              </div>
              <Error>{saveAtsCompanies.error}</Error>
            </>
          ) : null}

          <p className="label" style={{ marginTop: "1.5rem" }}>Descriptions fetched per cycle</p>
          <span className="hint">
            A posting with no description has no words to match, so it reaches nobody.
            This is what decides how fast a backlog of thin rows clears.
          </span>
          <select
            value={settings.describe_per_cycle}
            disabled={savePlatform.busy}
            aria-label="Descriptions fetched per cycle"
            onChange={(event) =>
              void savePlatform.run("describe_per_cycle", Number(event.target.value))}
            style={{ maxWidth: "10rem", marginTop: "0.5rem" }}
          >
            {[50, 100, 200, 400, 800].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>

          <p className="label" style={{ marginTop: "1.5rem" }}>Jobs kept for</p>
          <span className="hint">
            How long a job stays before it is moved to the archive file. Jobs somebody applied to
            or hid stay regardless. Jobs are what fill the database, so this is the control when it
            nears its size.
          </span>
          <select
            value={settings.corpus_retention_days}
            disabled={savePlatform.busy}
            aria-label="Jobs kept for"
            onChange={(event) =>
              void savePlatform.run("corpus_retention_days", Number(event.target.value))}
            style={{ maxWidth: "10rem", marginTop: "0.5rem" }}
          >
            {[7, 10, 14, 30, 60, 90].map((n) => (
              <option key={n} value={n}>{n} days</option>
            ))}
          </select>

          <p className="label" style={{ marginTop: "1.5rem" }}>Database size</p>
          <span className="hint">
            The size of the MongoDB Atlas tier. An alert goes out at 80% of it, before Atlas stops
            accepting writes. Change it when the tier changes.
          </span>
          <select
            value={settings.database_budget_mb}
            disabled={savePlatform.busy}
            aria-label="Database size"
            onChange={(event) =>
              void savePlatform.run("database_budget_mb", Number(event.target.value))}
            style={{ maxWidth: "10rem", marginTop: "0.5rem" }}
          >
            {[512, 2048, 5120, 10240].map((n) => (
              <option key={n} value={n}>{n < 1024 ? `${n} MB` : `${n / 1024} GB`}</option>
            ))}
          </select>

          <p className="label" style={{ marginTop: "1.5rem" }}>LinkedIn accounts per tick</p>
          <span className="hint">
            How many users&apos; LinkedIn passes run at once. Not a per-user setting — each account
            uses its own proxy, so this is the platform not issuing hundreds of LinkedIn requests
            simultaneously. At this rate a full rotation of every account takes{" "}
            {Math.ceil((users?.length ?? 1) / Math.max(settings.linkedin_concurrent_users, 1))}{" "}
            tick(s).
          </span>
          <select
            value={settings.linkedin_concurrent_users}
            disabled={savePlatform.busy}
            aria-label="LinkedIn accounts per tick"
            onChange={(event) =>
              void savePlatform.run("linkedin_concurrent_users", Number(event.target.value))}
            style={{ maxWidth: "10rem", marginTop: "0.5rem" }}
          >
            {[2, 4, 8, 16, 32].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>

          <p className="label" style={{ marginTop: "1.5rem" }}>Built documents kept for</p>
          <span className="hint">
            How long a CV or cover letter waits to be downloaded before it is deleted (D9). Longer
            lets a person come back for it; shorter keeps less of anybody&apos;s CV on this server.
          </span>
          <select
            value={settings.document_keep_hours}
            disabled={savePlatform.busy}
            aria-label="Built documents kept for"
            onChange={(event) =>
              void savePlatform.run("document_keep_hours", Number(event.target.value))}
            style={{ maxWidth: "10rem", marginTop: "0.5rem" }}
          >
            {[1, 6, 24].map((n) => (
              <option key={n} value={n}>{n === 1 ? "1 hour" : `${n} hours`}</option>
            ))}
          </select>
          <Error>{savePlatform.error}</Error>

          <p className="label" style={{ marginTop: "1.5rem" }}>AI document models</p>
          <span className="hint">
            The model each provider writes AI documents with. The CV prompts were measured on
            Claude Haiku; the others run them unmeasured. A name the provider does not know fails
            every AI document on it.
          </span>
          {["anthropic", "gemini", "openai"].map((provider) => (
            <label key={provider} className="small" style={{ display: "block", marginTop: "0.5rem" }}>
              {provider}{" "}
              <input
                defaultValue={settings.document_models[provider]}
                disabled={saveModel.busy}
                onBlur={(event) => void saveModel.run(provider, event.target.value.trim())}
                style={{ maxWidth: "16rem" }}
              />
            </label>
          ))}
          <Error>{saveModel.error}</Error>

          <p className="label" style={{ marginTop: "1.5rem" }}>Help chat</p>
          <span className="hint">
            Answers questions on the Help page, with the AI document models above.
          </span>
          <select
            value={settings.support_chat_enabled}
            disabled={savePlatform.busy}
            aria-label="Help chat"
            onChange={(event) =>
              void savePlatform.run("support_chat_enabled", Number(event.target.value))}
            style={{ maxWidth: "10rem", marginTop: "0.5rem" }}
          >
            <option value={0}>Off</option>
            <option value={1}>On</option>
          </select>
        </div>
      ) : null}</> : null}


      {tab === "corpus" ? <CorpusTab /> : null}
      {tab === "pipeline" ? <PipelineTab /> : null}
      {tab === "latency" ? <LatencyTab /> : null}

      {tab === "accounts" ? <>      <p className="kicker" style={{ marginTop: "2.5rem" }}>
        Accounts
      </p>
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Status</th>
            <th>Telegram</th>
            <th>Onboarded</th>
            <th>Last active</th>
            <th>Access</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            // The whole row opens the account.
            <tr key={user.id} className="row-link" onClick={() => void openUser.run(user.id)}>
              <td>{user.email}</td>
              <td className="job-sub">
                {user.status}
                {user.active ? "" : " · paused"}
              </td>
              <td className="job-sub">{user.telegram}</td>
              <td className="job-sub">{user.onboarded ? "yes" : "no"}</td>
              <td className="job-sub">{lastActive(user.last_active_at)}</td>
              {/* Both controls stop the click reaching the row, or granting access would also navigate. */}
              <td onClick={(event) => event.stopPropagation()}>
                <label className="switch" title="Operator access">
                  <input
                    type="checkbox"
                    checked={user.is_admin}
                    disabled={toggleAdmin.busy}
                    onChange={() => void toggleAdmin.run(user)}
                  />
                  <span>admin</span>
                </label>
              </td>
              <td onClick={(event) => event.stopPropagation()}>
                <button
                  type="button"
                  className="bell-act danger"
                  aria-label={`Delete ${user.email}`}
                  title="Delete this account"
                  onClick={() => { setDoomed(user); setTyped(""); }}
                >
                  <BinIcon />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </> : null}

      {/* The address has to be typed, and the server checks it too. */}
      {doomed ? (
        <div className="modal-backdrop" onClick={() => setDoomed(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <h2 style={{ marginTop: 0 }}>Delete this account?</h2>
            <p className="facts">
              Everything belonging to <strong>{doomed.email}</strong> goes: their profile files,
              labels, documents, models and history. It happens immediately and cannot be undone.
              The shared job corpus is not affected.
            </p>
            <label className="label" htmlFor="confirm-email">Type the address to confirm</label>
            <input
              id="confirm-email"
              value={typed}
              autoComplete="off"
              placeholder={doomed.email}
              onChange={(event) => setTyped(event.target.value)}
            />
            <Error>{removeUser.error}</Error>
            <div style={{ display: "flex", gap: "0.6rem", marginTop: "1rem" }}>
              <button
                className="danger-btn"
                disabled={
                  removeUser.busy || typed.trim().toLowerCase() !== doomed.email.toLowerCase()
                }
                onClick={() => void removeUser.run(doomed, typed.trim())}
              >
                {removeUser.busy ? "Deleting…" : "Delete permanently"}
              </button>
              <button className="secondary" onClick={() => setDoomed(null)}>Cancel</button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}


function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <dt>{value}</dt>
      <dd>{label}</dd>
    </div>
  );
}


/** What the shared corpus actually holds, and the terms filling it. */
function CorpusTab() {
  const [page, setPage] = useState<CorpusPage | null>(null);
  const [keywords, setKeywords] = useState<Keywords | null>(null);
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [term, setTerm] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  // Searching the term lists is a different thing from searching the corpus.
  const [termQuery, setTermQuery] = useState("");
  const [operatorPage, setOperatorPage] = useState(0);
  const [userPage, setUserPage] = useState(0);

  const load = async (offset = 0) => {
    const query = new URLSearchParams({ q, source, days: "30", offset: String(offset) });
    setPage(await api.get<CorpusPage>(`/admin/corpus?${query}`));
  };
  const loadKeywords = async () => setKeywords(await api.get<Keywords>("/admin/keywords"));

  useEffect(() => {
    void load().catch(() => setFailed("Could not read the corpus."));
    void loadKeywords().catch(() => undefined);
    // Deliberately once: searching is an explicit action.
  }, []);

  const addTerm = useAction(async () => {
    await api.post("/admin/keywords", { keyword: term });
    setTerm("");
    await loadKeywords();
  });

  const dropTerm = useAction(async (id: number) => {
    await api.del(`/admin/keywords/${id}`);
    await loadKeywords();
  });

  // Filtered and paged in the browser.
  const needle = termQuery.trim().toLowerCase();
  const operatorTerms = (keywords?.operator ?? []).filter(
    (row) => !needle || row.keyword.includes(needle));
  const userTerms = (keywords?.users ?? []).filter((row) => !needle || row.keyword.includes(needle));
  const operatorShown = operatorTerms.slice(operatorPage * TERMS_PER_PAGE,
                                            (operatorPage + 1) * TERMS_PER_PAGE);
  const userShown = userTerms.slice(userPage * TERMS_PER_PAGE, (userPage + 1) * TERMS_PER_PAGE);

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Corpus</h2>
      <p className="hint">
        Every posting the platform has collected, shared by all accounts. A new user can only train
        on what is already in here for their field, which is why seeding it ahead of them matters.
      </p>

      <form
        className="row"
        style={{ margin: "1rem 0" }}
        onSubmit={(event) => {
          event.preventDefault();
          void load().catch(() => setFailed("Could not read the corpus."));
        }}
      >
        <input
          value={q}
          placeholder="Title or company"
          onChange={(event) => setQ(event.target.value)}
          style={{ maxWidth: "18rem" }}
        />
        <select value={source} onChange={(event) => setSource(event.target.value)}
                style={{ maxWidth: "12rem" }}>
          <option value="">Every source</option>
          {(page?.sources ?? []).map((row) => (
            <option key={row.source} value={row.source}>
              {row.source} ({row.count.toLocaleString()})
            </option>
          ))}
        </select>
        <button type="submit" className="secondary">Search</button>
      </form>

      {failed ? <p className="error">{failed}</p> : null}

      {page ? (
        <>
          <p className="facts">
            {page.total.toLocaleString()} postings in the last 30 days
            {q || source ? " matching this search" : ""}.
          </p>
          <div className="table-scroll">
            <table className="history">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Company</th>
                  <th>Where</th>
                  <th>Source</th>
                  <th>Seen</th>
                  {/* A posting with no description has no words to match, so it reaches nobody. */}
                  <th>Usable</th>
                </tr>
              </thead>
              <tbody>
                {page.jobs.map((job) => (
                  <tr key={job.id}>
                    <td><a href={job.url} target="_blank" rel="noopener noreferrer">{job.title}</a></td>
                    <td>{job.company ?? "—"}</td>
                    <td>{job.is_remote ? "Remote" : (job.location ?? "—")}</td>
                    <td>{job.source ?? "—"}</td>
                    <td>
                      {job.first_seen_at
                        ? new Date(job.first_seen_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td style={{ color: job.described ? "var(--score)" : "var(--dim)" }}>
                      {job.described ? "yes" : "thin"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {page.total > page.page_size ? (
            <div className="row" style={{ marginTop: "0.8rem" }}>
              <button
                className="secondary"
                disabled={page.offset === 0}
                onClick={() => void load(Math.max(page.offset - page.page_size, 0))}
              >
                Previous
              </button>
              <button
                className="secondary"
                disabled={page.offset + page.page_size >= page.total}
                onClick={() => void load(page.offset + page.page_size)}
              >
                Next
              </button>
            </div>
          ) : null}
        </>
      ) : null}

      <h2 style={{ marginTop: "2.5rem" }}>Search terms</h2>
      <p className="hint">
        What the harvest looks for. Add terms to fill the corpus for fields nobody has signed up for
        yet, so the first person who does has something to train on.
      </p>

      <input
        value={termQuery}
        placeholder="Filter terms — check before adding a duplicate"
        onChange={(event) => {
          setTermQuery(event.target.value);
          setOperatorPage(0);
          setUserPage(0);
        }}
        style={{ maxWidth: "22rem", marginBottom: "0.8rem" }}
      />

      <form
        className="row"
        style={{ margin: "0.8rem 0" }}
        onSubmit={(event) => {
          event.preventDefault();
          if (term.trim()) void addTerm.run();
        }}
      >
        <input
          value={term}
          placeholder="e.g. stress engineer"
          onChange={(event) => setTerm(event.target.value)}
          style={{ maxWidth: "18rem" }}
        />
        <button type="submit" className="secondary" disabled={addTerm.busy || !term.trim()}>
          {addTerm.busy ? "Adding…" : "Add term"}
        </button>
      </form>
      <Error>{addTerm.error ?? dropTerm.error}</Error>

      <div className="term-grid">
        {operatorShown.map((row) => (
          <div key={row.id} className="source-row">
            <span className="source-name">
              {row.keyword}
              {row.location ? <span className="small muted"> · {row.location}</span> : null}
            </span>
            <button
              type="button"
              className="icon-btn"
              title="Remove this term"
              aria-label={`Remove ${row.keyword}`}
              disabled={dropTerm.busy}
              onClick={() => void dropTerm.run(row.id)}
            >
              <BinIcon />
            </button>
          </div>
        ))}
      </div>
      {keywords && !operatorTerms.length ? (
        <p className="facts">
          {needle
            ? "No added term matches that."
            : "No terms added yet. The harvest is searching for whatever users ask for."}
        </p>
      ) : null}
      <Pager
        page={operatorPage}
        total={operatorTerms.length}
        onPage={setOperatorPage}
      />

      {/* Shown, never removable. */}
      <h2 style={{ marginTop: "2rem" }}>What users are searching for</h2>
      <p className="hint">
        Theirs, not yours. Listed so you can see what the harvest is spending its budget on.
      </p>
      <div className="term-grid">
        {userShown.map((row) => (
          <div key={row.keyword} className="source-row" title={row.accounts.join(", ")}>
            <span className="source-name">{row.keyword}</span>
            <span className="small muted">{row.accounts.length}</span>
          </div>
        ))}
      </div>
      {keywords && !userTerms.length ? (
        <p className="facts">
          {needle ? "No user term matches that." : "Nobody is searching for anything yet."}
        </p>
      ) : null}
      <Pager page={userPage} total={userTerms.length} onPage={setUserPage} />
    </>
  );
}


/** Where the time goes, one row per zone the posting crosses. */

/** Where the jobs are, right now, streamed rather than asked for. */
function PipelineTab() {
  const [report, setReport] = useState<PipelineReport | null>(null);
  const [connected, setConnected] = useState(false);
  // The previous frame, so a count that moved can say so.
  const previous = useRef<Record<string, number | null>>({});

  useEffect(() => {
    const source = new EventSource("/admin/pipeline/stream");
    source.onmessage = (event) => {
      const data = JSON.parse(event.data) as PipelineReport & { error?: string };
      if (data.error) return; // one bad frame; the next is a second away
      setReport((was) => {
        if (was) {
          previous.current = Object.fromEntries(was.stages.map((s) => [s.key, s.waiting]));
        }
        return data;
      });
      setConnected(true);
    };
    // `EventSource` reconnects by itself.
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, []);

  if (!report) return <p className="muted">Opening the live pipeline…</p>;

  const busiest = Math.max(1, ...report.stages.map((s) => s.waiting ?? 0));
  const lanes = report.lanes.filter((lane) => lane.ready || lane.running || lane.dead);
  // Same filter, same reason: an account whose only rows are `done` is not something to look at.
  // Dead first, because that is the row somebody has to act on.
  const accounts = (report.lane_accounts ?? [])
    .filter((row) => row.ready || row.running || row.dead)
    .sort((a, b) => b.dead - a.dead || a.lane.localeCompare(b.lane));

  return (
    <>
      <h2>
        Where the jobs are
        <span className={`pulse ${connected ? "on" : "off"}`}
              title={connected ? "live" : "reconnecting"} />
      </h2>
      <p className="hint" style={{ maxWidth: "62ch" }}>
        One row per stage, in the order a posting moves through them, updating every second.{" "}
        <strong>Waiting</strong> is how many are sitting in that stage now; <strong>entered</strong>{" "}
        is how many arrived in the last {report.window_hours} hours. A stage turns amber after{" "}
        {report.amber_after_min} minutes of the oldest row waiting and red after{" "}
        {report.red_after_min} — the tick that should have cleared it runs every{" "}
        {report.amber_after_min / 2} minutes.
      </p>

      <div className="pipeline">
        {report.stages.map((stage) => {
          const was = previous.current[stage.key];
          // Only a change worth seeing.
          const moved =
            was !== undefined && was !== null && stage.waiting !== null ? stage.waiting - was : 0;
          return (
            <div key={stage.key} className={`pipeline-row ${stage.verdict}`}>
              <div className="pipeline-head">
                <strong>{stage.label}</strong>
                <span className="small muted">{stage.what}</span>
              </div>
              <div className="pipeline-bar-wrap">
                {/* Width is relative to the busiest stage. */}
                <div className="pipeline-bar"
                     style={{ width: `${Math.round(((stage.waiting ?? 0) / busiest) * 100)}%` }} />
              </div>
              <div className="pipeline-numbers">
                <span className={`pipeline-waiting${moved ? (moved < 0 ? " fell" : " rose") : ""}`}>
                  {stage.waiting === null ? "—" : stage.waiting.toLocaleString()}
                </span>
                <span className="small muted">
                  waiting
                  {moved ? <em className="delta">{moved > 0 ? `+${moved}` : moved}</em> : null}
                </span>
                <span className="pipeline-entered">
                  {stage.entered === null ? "—" : stage.entered.toLocaleString()}
                </span>
                <span className="small muted">in {report.window_hours}h</span>
                <span className="small muted">
                  {stage.oldest_min === null ? "—" : `oldest ${minutes(stage.oldest_min)}`}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* The stages say where postings are; this says whether anything is turning the handle. */}
      <h3 style={{ marginTop: "1.6rem" }}>The queue, right now</h3>
      {lanes.length === 0 ? (
        <p className="hint">Every lane is idle. Between ticks that is the normal state.</p>
      ) : (
        <div className="lanes">
          {lanes.map((lane) => (
            <div key={lane.lane} className={`lane-chip${lane.dead ? " down" : ""}`}>
              <strong>{lane.lane}</strong>
              {lane.running ? <span className="lane-running">{lane.running} running</span> : null}
              {lane.ready ? <span className="lane-ready">{lane.ready} queued</span> : null}
              {lane.dead ? <span className="lane-dead">{lane.dead} dead</span> : null}
            </div>
          ))}
        </div>
      )}

      {/* Whose work it is. A lane is per-account, so "2 dead" is either one account in a broken
          state or two unrelated problems, and the chips above cannot tell you which. Finding out
          used to mean a psql session against production. */}
      <h3 style={{ marginTop: "1.6rem" }}>Who it is running for</h3>
      {accounts.length === 0 ? (
        <p className="hint">
          No account has work in a lane right now. Nothing queued, nothing running, nothing dead.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Lane</th>
              <th>Account</th>
              <th>Running</th>
              <th>Queued</th>
              <th>Dead</th>
              <th>Done ({report.window_hours}h)</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((row) => (
              <tr key={`${row.lane}:${row.user_id}`}>
                <td><strong>{row.lane}</strong></td>
                <td>
                  <a href={`/admin/users/${row.user_id}`}>{row.email}</a>
                  {/* The error is the whole reason to look, so it is not behind a click. */}
                  {row.last_error ? <div className="hint">{row.last_error}</div> : null}
                </td>
                <td>{row.running || "—"}</td>
                <td>{row.ready || "—"}</td>
                <td>{row.dead ? <span className="lane-dead">{row.dead}</span> : "—"}</td>
                <td>{row.done || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function LatencyTab() {
  const [report, setReport] = useState<LatencyReport | null>(null);
  const [hours, setHours] = useState(24);
  // One row open at a time.
  const [open, setOpen] = useState<string | null>(null);
  // Kept per stage after the first fetch.
  const [details, setDetails] = useState<Record<string, StageDetail>>({});
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    setReport(null);
    setDetails({});
    setOpen(null);
    void api
      .get<LatencyReport>(`/admin/latency?hours=${hours}`)
      .then(setReport)
      .catch(() => setFailed("Could not read the pipeline timings."));
  }, [hours]);

  const toggle = (key: string) => {
    if (open === key) {
      setOpen(null);
      return;
    }
    setOpen(key);
    if (details[key]) return;
    void api
      .get<StageDetail>(`/admin/latency/${key}?hours=${hours}`)
      .then((detail) => setDetails((held) => ({ ...held, [key]: detail })))
      .catch(() => setFailed("Could not read that stage."));
  };

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Where the time goes</h2>
      <p className="hint">
        A posting arrives as a link and leaves as a card in Telegram. Every row below is one
        boundary in between — somewhere work is picked up, done, and handed on. Open a row for what
        that zone is waiting on.
      </p>

      <div className="row" style={{ margin: "1rem 0", alignItems: "center" }}>
        <span className="kicker" style={{ margin: 0 }}>Window</span>
        {([[6, "6 hours"], [24, "24 hours"], [168, "7 days"]] as [number, string][]).map(
          ([value, label]) => (
            <button
              key={value}
              type="button"
              className={hours === value ? "chip on" : "chip"}
              onClick={() => setHours(value)}
            >
              {label}
            </button>
          ))}
      </div>

      {failed ? <p className="error">{failed}</p> : null}
      {!report && !failed ? <p className="facts">Measuring…</p> : null}

      {report ? (
        <div className="table-scroll">
          <table className="history stages">
            <thead>
              <tr>
                <th />
                <th>Stage</th>
                <th className="num">Median</th>
                <th className="num">p90</th>
                <th className="num">p99</th>
                {/* A stage with a healthy p90 and a forty-minute tail has something worth looking at. */}
                <th className="num">Worst</th>
                <th className="num">Volume</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {report.stages.map((stage) => {
                const isOpen = open === stage.key;
                return (
                  <Fragment key={stage.key}>
                    <tr
                      className={`row-link${stage.key === "found_to_delivered" ? " on" : ""}`}
                      onClick={() => toggle(stage.key)}
                      aria-expanded={isOpen}
                    >
                      <td className="caret">
                        <Caret open={isOpen} />
                      </td>
                      <td>
                        {stage.label}
                        <span className="job-sub"> · {stage.zone}</span>
                      </td>
                      <td className="num">{minutes(stage.p50)}</td>
                      <td className="num">{minutes(stage.p90)}</td>
                      <td className="num">{minutes(stage.p99)}</td>
                      <td className="num">{minutes(stage.worst)}</td>
                      <td className="num">{stage.samples.toLocaleString()}</td>
                      <td>
                        <span className={`verdict ${stage.verdict}`}>{verdictWord(stage)}</span>
                      </td>
                    </tr>
                    {isOpen ? (
                      <tr className="drill">
                        <td colSpan={8}>
                          <StageDrill stage={stage} detail={details[stage.key]} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}


      {/* "Not enough data" is a real answer, not a failure. */}
      <p className="hint" style={{ marginTop: "0.9rem" }}>
        Every row is judged on its p90, not its median — half the jobs arriving promptly is not the
        claim this product makes, and a median hides exactly the tail somebody loses a role in. A
        row with too few completed examples says so rather than showing a colour.
      </p>
    </>
  );
}

/** The zone, expanded: waiting versus running, what is queued behind it, and the last day's shape. */
function StageDrill({ stage, detail }: { stage: StageRow; detail: StageDetail | undefined }) {
  if (!detail) return <p className="facts">Reading this stage…</p>;

  return (
    <div className="drill-body">
      <p className="drill-what">{stage.what}</p>
      {stage.caveat ? <p className="hint drill-caveat">{stage.caveat}</p> : null}

      <div className="stat-row">
        {/* Waiting and running are two different failures. */}
        {detail.lane ? (
          <>
            <Tile
              label="Waiting for a worker"
              value={seconds(detail.lane.wait_p90)}
              sub={`p90 · median ${seconds(detail.lane.wait_p50)}`}
              note="Long here means too few workers, or a lane stuck behind a long job."
            />
            <Tile
              label="Doing the work"
              value={seconds(detail.lane.run_p90)}
              sub={`p90 · worst ${seconds(detail.lane.run_max)}`}
              note="Long here means the work itself got slower, which is a different fix."
            />
          </>
        ) : null}
        {detail.lane_name && !detail.lane ? (
          <Tile
            label={`${detail.lane_name} lane`}
            value="—"
            sub="nothing completed"
            note="No job in this lane finished inside the window."
          />
        ) : null}
        {detail.backlog ? (
          <Tile
            label="Queued at this door"
            value={detail.backlog.waiting.toLocaleString()}
            sub={
              detail.backlog.oldest_min !== null
                ? `oldest waiting ${minutes(detail.backlog.oldest_min)}`
                : "nothing waiting"
            }
            note="The percentiles describe what got through. This is what has not."
          />
        ) : null}
        <Tile
          label="Completed in window"
          value={stage.samples.toLocaleString()}
          sub={`last ${detail.window_hours} hours`}
          note={stage.judged
            ? `Amber over ${stage.amber_after_min}m, red over ${stage.red_after_min}m.`
            : "Reported without a verdict."}
        />
      </div>

      <p className="kicker" style={{ marginTop: "1.4rem" }}>Hour by hour</p>
      <StageSeries series={detail.series} amber={stage.amber_after_min}
                   red={stage.red_after_min} />

      {detail.reasoning ? (
        <p className="hint drill-why">
          <strong>Why that threshold. </strong>
          {detail.reasoning}
        </p>
      ) : null}
    </div>
  );
}

/** One number with the sentence that says what to do about it. */
function Tile({ label, value, sub, note }: {
  label: string;
  value: string;
  sub: string;
  note: string;
}) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      <span className="stat-sub">{sub}</span>
      <span className="stat-note">{note}</span>
    </div>
  );
}

/** The stage's p90 per hour, against its own thresholds. */
function StageSeries({ series, amber, red }: {
  series: SeriesPoint[];
  amber: number | null;
  red: number | null;
}) {
  const first = series[0];
  if (!first) {
    return <p className="facts">Nothing completed this stage in the window.</p>;
  }
  const last = series[series.length - 1] ?? first;

  const width = 720;
  const height = 132;
  const padTop = 10;
  const padBottom = 20;
  const padRight = 58; // Room for the threshold labels, which sit outside the plot.
  const plotWidth = width - padRight;
  const plotHeight = height - padTop - padBottom;

  const peak = Math.max(...series.map((point) => point.p90 ?? 0), red ?? 0, amber ?? 0, 1);
  // Headroom, so the tallest bar and a threshold sitting near it are not touching the frame.
  const ceiling = peak * 1.15;
  const y = (value: number) => padTop + plotHeight - (value / ceiling) * plotHeight;

  const gap = 2;
  const barWidth = Math.max((plotWidth - gap * (series.length - 1)) / series.length, 1.5);
  const tallest = series.reduce<SeriesPoint>(
    (best, point) => ((point.p90 ?? 0) > (best.p90 ?? 0) ? point : best), first);

  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label="Stage p90 by hour, in minutes">
      {/* The baseline, and nothing else. A grid behind eighteen thin bars is furniture. */}
      <line x1="0" y1={padTop + plotHeight} x2={plotWidth} y2={padTop + plotHeight}
            stroke="var(--faint)" strokeWidth="1" />

      {[["amber", amber, "#d08a34"], ["red", red, "#d4574e"]].map(([name, value, colour]) => {
        const level = value as number | null;
        if (level === null || level > ceiling) return null;
        return (
          <g key={name as string}>
            <line x1="0" y1={y(level)} x2={plotWidth} y2={y(level)}
                  stroke={colour as string} strokeWidth="1" strokeDasharray="3 4" opacity="0.7" />
            <text x={plotWidth + 6} y={y(level) + 3.5} className="spark-label"
                  fill={colour as string}>
              {name === "amber" ? "amber" : "red"} {level}m
            </text>
          </g>
        );
      })}

      {series.map((point, index) => {
        const value = point.p90 ?? 0;
        const x = index * (barWidth + gap);
        const top = y(value);
        const barHeight = padTop + plotHeight - top;
        return (
          <path key={point.at} d={bar(x, top, barWidth, barHeight, 4)} fill="var(--dim)">
            {/* The hover layer an SVG chart owes the reader. */}
            <title>
              {hourLabel(point.at)} · p90 {minutes(point.p90)} · median {minutes(point.p50)} ·{" "}
              {point.samples.toLocaleString()} jobs
            </title>
          </path>
        );
      })}

      {/* One direct label, on the worst hour. */}
      {tallest.p90 ? (
        <text
          x={Math.min(series.indexOf(tallest) * (barWidth + gap) + barWidth / 2, plotWidth - 24)}
          y={y(tallest.p90) - 5}
          className="spark-peak"
          textAnchor="middle"
        >
          {minutes(tallest.p90)}
        </text>
      ) : null}

      <text x="0" y={height - 6} className="spark-label">{hourLabel(first.at)}</text>
      {series.length > 1 ? (
        <text x={plotWidth} y={height - 6} className="spark-label" textAnchor="end">
          {hourLabel(last.at)}
        </text>
      ) : null}
    </svg>
  );
}

/** A bar with its top corners rounded and its base square on the axis. */
function bar(x: number, y: number, width: number, height: number, radius: number): string {
  const r = Math.min(radius, width / 2, Math.max(height, 0));
  const base = y + Math.max(height, 0);
  return `M${x},${base} L${x},${y + r} Q${x},${y} ${x + r},${y} `
       + `L${x + width - r},${y} Q${x + width},${y} ${x + width},${y + r} `
       + `L${x + width},${base} Z`;
}

/** The disclosure arrow. */
function Caret({ open }: { open: boolean }) {
  return (
    <span className={open ? "caret-icon open" : "caret-icon"} aria-hidden="true">›</span>
  );
}

/** Minutes, in the unit a person would say them in. */
function minutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return "<1m";
  if (value < 90) return `${Math.round(value)}m`;
  const whole = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return rest ? `${whole}h ${rest}m` : `${whole}h`;
}

/** Lane timings arrive in seconds — the work inside a lane is sub-minute when it is healthy. */
function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return "<1s";
  if (value < 90) return `${Math.round(value)}s`;
  return minutes(value / 60);
}

function hourLabel(at: string): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Colour is never the only signal, so every pill carries its word. */
function verdictWord(stage: StageRow): string {
  if (!stage.judged) return "reported, not judged";
  if (stage.verdict === "ok") return "healthy";
  if (stage.verdict === "warn") return `slow, over ${stage.amber_after_min}m`;
  if (stage.verdict === "down") return `too slow, over ${stage.red_after_min}m`;
  return "not enough data";
}

/** The result ceiling for one source, or nothing where the source has no such control. */
function Results({ source, save }: {
  source: Source;
  save: { busy: boolean; run: (name: string, value: number) => Promise<unknown> };
}) {
  if (source.results_per_query === null) {
    return <span className="small muted">—</span>;
  }
  return (
    <select
      value={source.results_per_query}
      disabled={save.busy}
      aria-label={`${source.name} results per query`}
      onChange={(event) => void save.run(source.name, Number(event.target.value))}
    >
      {[20, 30, 40, 50, 100, 200].map((n) => (
        <option key={n} value={n}>{n}</option>
      ))}
    </select>
  );
}

/** How deep a paginated source walks in one cycle. */
function Pages({ source, save }: {
  source: Source;
  save: { busy: boolean; run: (name: string, value: number) => Promise<unknown> };
}) {
  if (source.pages_per_cycle === null) {
    return <span className="small muted">—</span>;
  }
  return (
    <select
      value={source.pages_per_cycle}
      disabled={save.busy}
      aria-label={`${source.name} pages per cycle`}
      onChange={(event) => void save.run(source.name, Number(event.target.value))}
    >
      {[1, 2, 3, 5, 8, 12, 20].map((n) => (
        <option key={n} value={n}>{n}</option>
      ))}
    </select>
  );
}

/** How many terms a page shows. */
const TERMS_PER_PAGE = 20;

/** Previous/next for a list held in the browser. */
function Pager({
  page,
  total,
  onPage,
}: {
  page: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.ceil(total / TERMS_PER_PAGE);
  if (pages <= 1) return null;
  return (
    <div className="row" style={{ marginTop: "0.6rem", alignItems: "center" }}>
      <button className="secondary" disabled={page === 0} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <span className="small muted">
        {page + 1} of {pages} · {total.toLocaleString()} terms
      </span>
      <button
        className="secondary"
        disabled={page + 1 >= pages}
        onClick={() => onPage(page + 1)}
      >
        Next
      </button>
    </div>
  );
}
