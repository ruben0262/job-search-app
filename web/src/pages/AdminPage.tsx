/**
 * The operator panel — what needs a person, and who the accounts are.
 *
 * **Read-only, deliberately, for now.** `OPERATIONS_PLAN.md` §5 designs a console that *acts*
 * (suspend, requeue, resend, with an audit trail), and at two accounts `deploy/scripts/` covers
 * that ground from a shell. What was actually missing was the *read* half: the numbers existed at
 * `/admin/health` and nothing rendered them, so checking on the platform meant an SSH session.
 *
 * **Alerts first, numbers second.** A page of numbers is something an operator scans; an alert
 * list is something they act on, and §1's whole argument is that a failure nobody is told about is
 * a failure nobody fixes. So the conditions that need a person are at the top, in the order the
 * server ranked them, each with the action it named.
 *
 * Visible only when `/dashboard` says `is_admin`. That is a rendering hint — the API refuses
 * `/admin/*` on its own, from `ADMIN_EMAILS`, and this page could not read anything without it.
 */
import { Fragment, useEffect, useState } from "react";

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
  corpus_embedded: number;
  postgres_bytes: number;
  connections: number;
  alerts: Alert[];
  latency: {
    window_hours: number;
    found_to_scored: Span;
    scored_to_delivered: Span;
    found_to_delivered: Span;
  };
};

type AdminUser = {
  id: string;
  email: string;
  status: string;
  active: boolean;
  plan: string;
  telegram: string;
  model_version: number | null;
  onboarded: boolean;
  is_admin: boolean;
};

/** One latency stage. `verdict` and the thresholds come from `libs.ops.latency`, so the page
 *  renders a judgement rather than re-deriving one that could disagree with the API. Optional
 *  because an older response carries the numbers without them. */
type Span = {
  p50: number | null;
  p90: number | null;
  p99: number | null;
  samples: number;
  verdict?: "ok" | "warn" | "down" | "unknown";
  amber_after_min?: number;
  red_after_min?: number;
};

/** One zone of the pipeline, as the table renders it collapsed. `judged` is separate from the
 *  verdict on purpose: a stage can be measured and deliberately uncoloured, and that is not the
 *  same thing as having too little data to say. */
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

/** What one expanded row shows. Fetched on expansion rather than with the table — each piece costs
 *  a query, and only one row is ever open. */
type StageDetail = {
  key: string;
  label: string;
  zone: string;
  what: string;
  caveat: string | null;
  window_hours: number;
  lane_name: string | null;
  // Seconds, from the queue's own timestamps. Absent when no job in that lane finished in the
  // window, and absent entirely for the stages that run on a timer rather than the queue.
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

/** One stage as it stands right now. `entered` and `oldest_min` are `null` where the number is
 *  genuinely unavailable — the page shows a dash, never a zero, because zero is a claim. */
type PipelineStage = {
  key: string;
  label: string;
  what: string;
  entered: number | null;
  waiting: number;
  oldest_min: number | null;
  verdict: "ok" | "warn" | "down";
};

type PipelineReport = {
  window_hours: number;
  stages: PipelineStage[];
  amber_after_min: number;
  red_after_min: number;
};

type Settings = {
  delivery_interval_min: number;
  source_freshness_hours: Record<string, number>;
  default_freshness_hours: number;
  source_queries_per_cycle: Record<string, number>;
  source_results_per_query: Record<string, number>;
  source_pages_per_cycle: Record<string, number>;
  ats_companies_per_cycle: number;
  describe_per_cycle: number;
  linkedin_concurrent_users: number;
};

type Source = {
  name: string;
  hours: number;
  queries_per_cycle: number;
  /** `null` where the source has no result cap to set — it serves a feed or counts pages. */
  results_per_query: number | null;
  /** `null` where the source fetches one response rather than walking pages. */
  pages_per_cycle: number | null;
  /** How many distinct queries this source currently has. 0 means it takes none. */
  wanted: number;
};

/** The ATS ceiling and the number it is a ceiling on. The token list grows from harvested
 *  apply-URLs, so the limit means nothing without the total beside it. */
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
  embedded: boolean;
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

  // One source, one number. Written as a whole map because that is what the setting is — reading
  // it back from the response keeps the panel honest about what was actually stored.
  // Same shape as the freshness control beside it: write the whole map, read back what was
  // stored. One mechanism for both, because two would drift.
  // One writer for the plain scalar settings. Two would drift, and these behave identically.
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

  // The alert key is `dead_letter:<lane>`, so the lane comes from the alert being dismissed rather
  // than from a second piece of state that could disagree with which banner was clicked.
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

  const saveCadence = useAction(async (minutes: number) => {
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/delivery_interval_min", { value: minutes });
    setSettings(body.settings);
  });

  // **The account opens as its own page, at its own address.** It used to unfold inside this
  // list, which meant no link to send anybody, no back button, and the thing you opened rendering
  // underneath the table you opened it from.
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

  const embedded = health.corpus_rows
    ? Math.round((health.corpus_embedded / health.corpus_rows) * 100)
    : 0;

  return (
    <>
      <p className="kicker">Operator</p>
      <div className="spread">
        <h1>Platform</h1>
        <span className="small muted">
          checked {new Date(health.checked_at).toLocaleTimeString()}
        </span>
      </div>

      {/* **Tabs, because this page had six stacked sections and no shape.** An operator arrives
          with one question at a time: is anything broken, what is the platform set to, how fast is
          it, who are the accounts. Stacking them meant scrolling past four answers to reach the
          fifth, and it had already produced a real defect: the "Queue" heading sat immediately
          above the settings card rather than above its own table. */}
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
      {/* What needs a person. Empty is the good state and says so, rather than rendering nothing
          and leaving the reader unsure whether it loaded. */}
      {health.alerts.length === 0 ? (
        <p className="muted">Nothing needs a person right now.</p>
      ) : (
        <div style={{ marginBottom: "2rem" }}>
          {health.alerts.map((alert) => (
            <div key={alert.key} className={`banner ${alert.severity === "page" ? "error" : "warning"}`}
                 style={{ marginBottom: "0.6rem" }}>
              {/* **Only the dead-letter alert is dismissable, and it is dismissed by clearing the
                  rows it counts — not by hiding the banner.** A general "hide this alert" control
                  would let a real outage be silenced with one click and stay silenced; here the
                  alert goes because its cause is gone, which is the only honest way for an alert
                  to disappear. Every other condition clears when the condition does. */}
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
        <Stat value={`${embedded}%`} label="embedded" />
        <Stat value={`${health.users_active}/${health.users_total}`} label="active users" />
        <Stat value={`${(health.postgres_bytes / 1024 ** 3).toFixed(1)} GB`} label="postgres" />
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
              {/* Dead letters are the only number here worth a colour: everything else is a
                  reading, and a dead job is work that has stopped and will not restart. */}
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

          <label className="label" htmlFor="cadence">Delivery cadence</label>
          <span className="hint">
            How often every user&apos;s feed is sent. The same for everyone, a longer cadence
            spends the lead time this product exists to give.
          </span>
          <select
            id="cadence"
            value={settings.delivery_interval_min}
            disabled={saveCadence.busy}
            onChange={(event) => void saveCadence.run(Number(event.target.value))}
            style={{ maxWidth: "18rem" }}
          >
            {[5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720, 1440].map((minutes) => (
              <option key={minutes} value={minutes}>
                {minutes < 60
                  ? `every ${minutes} minutes`
                  : minutes === 60
                    ? "every hour"
                    : minutes % 60 === 0
                      ? `every ${minutes / 60} hours`
                      : `every ${Math.floor(minutes / 60)}h ${minutes % 60}m`}
              </option>
            ))}
          </select>
          <Error>{saveCadence.error}</Error>

          <p className="label" style={{ marginTop: "1.5rem" }}>Per source</p>
          <span className="hint">
            <strong>Freshness</strong> is how recent a posting must be to qualify. LinkedIn lists
            within minutes; a company board is reached whenever our token list gets there, so the
            same window would keep 2% of it. <strong>Queries</strong> is how many times a cycle that
            source may be asked — per source, because rate limits are, and one shared number made
            unrelated boards queue behind each other. <strong>Wanted</strong> is how many it
            currently has: equal or under the ceiling means it is fully served each cycle.
          </span>
          <Error>{saveFreshness.error ?? saveQueries.error}</Error>
          <div className="sources">
            <div className="source-row source-head">
              <span className="source-name">source</span>
              <span>fresh</span>
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
                {/* Only the sources that take a query get a ceiling. A feed source is fetched once
                    whatever this said, so offering the control would be offering a lever attached
                    to nothing. */}
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
                    {/* Amber when the ceiling is below demand: that source is running behind and
                        the number beside it is why. */}
                    <span className={source.wanted > source.queries_per_cycle
                      ? "num behind" : "num muted"}>
                      {source.wanted}
                    </span>
                  </>
                ) : (
                  <>
                    {/* **A feed source issues exactly one request a cycle**, so the honest value is
                        1 rather than the word "feed" — which said "this control does not apply"
                        without saying what happens instead. Disabled because there is nothing to
                        choose: these boards have no search parameter, so a second request would
                        fetch the same page again. */}
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

          {/* **The ATS ceiling, with the number it is a ceiling on.** The token list grows on its
              own from harvested apply-URLs, so 400 says nothing until you know whether we hold 500
              or 12,000 — and that count was a query nobody ran. */}
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
            A posting with no description cannot be embedded, so it cannot be scored or delivered.
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
          <Error>{savePlatform.error}</Error>
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
            <th>Model</th>
            <th>Onboarded</th>
            <th>Access</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            // **The whole row opens the account.** It used to be the email that was clickable,
            // which is a target the width of the text and invisible until you find it.
            <tr key={user.id} className="row-link" onClick={() => void openUser.run(user.id)}>
              <td>{user.email}</td>
              <td className="job-sub">
                {user.status}
                {user.active ? "" : " · paused"}
              </td>
              <td className="job-sub">{user.telegram}</td>
              <td className="num job-sub">{user.model_version ? `v${user.model_version}` : "—"}</td>
              <td className="job-sub">{user.onboarded ? "yes" : "no"}</td>
              {/* Both controls stop the click reaching the row, or granting access would also
                  navigate, and a delete that opens a page underneath its own dialog is worse. */}
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

      {/* **The address has to be typed, and the server checks it too.** This destroys a person's
          profile, labels, documents and model with no undo, so a dialog somebody can dismiss by
          pressing return is not a confirmation. */}
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


/**
 * What the shared corpus actually holds, and the terms filling it.
 *
 * **No scores.** A score belongs to an account; this is the table every account draws from. The
 * question this answers is "is there anything in here for the next person who signs up", and until
 * now the only way to ask it was SQL — so nobody did, and the second account on the platform
 * stalled at ten labels because the corpus had nothing in their field.
 */
function CorpusTab() {
  const [page, setPage] = useState<CorpusPage | null>(null);
  const [keywords, setKeywords] = useState<Keywords | null>(null);
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [term, setTerm] = useState("");
  const [failed, setFailed] = useState<string | null>(null);
  // Searching the term lists is a different thing from searching the corpus: these are short
  // strings and the whole list is already in memory, so it filters as you type.
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
    // Deliberately once: searching is an explicit action, not something that fires per keystroke
    // against a table this size.
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

  // **Filtered and paged in the browser.** Both lists are small enough to hold — a few hundred
  // terms — and the point of the search box is to answer "is this already in here" before adding a
  // duplicate, which wants an answer as you type rather than after a round trip.
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
                  {/* An unembedded row is invisible to every user, and a corpus full of them looks
                      healthy from a row count alone. */}
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
                    <td style={{ color: job.embedded ? "var(--score)" : "var(--dim)" }}>
                      {job.embedded ? (job.described ? "yes" : "thin") : "not embedded"}
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

      {/* **Shown, never removable.** Somebody's feed is built from these. An operator seeing them is
          support; an operator able to delete one is an outage that looks like a setting, so there is
          no control here and no route behind it. */}
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


/**
 * Where the time goes, one row per zone the posting crosses.
 *
 * **The end-to-end number is the one worth publishing and the one least useful for fixing
 * anything.** "A posting took 36 minutes" does not say which of six handoffs ate them, and a stuck
 * describe worker, an embedder behind a flood, a score lane with no free worker and a delivery
 * cadence are four unrelated problems wearing the same number.
 *
 * So a posting's journey is the table: it arrives as a link, leaves as a card, and each row is one
 * boundary where work is picked up, done, and handed on. Collapsed, a row says *which* zone is the
 * problem. Expanded, it says *why that zone* — and the things that answer that are different in
 * kind from percentiles, which is exactly why they sit behind a disclosure rather than in six more
 * columns nobody reads (ROADMAP 6p).
 */

/** **Where the jobs are, right now.** The latency page says how long each handoff took for postings
 *  that already made it through; this says how many are sitting at each stage at this moment.
 *
 *  Every outage this platform has had was a stage quietly stopping while the numbers around it
 *  looked fine — six hours of ingestion behind an invisible filter, LinkedIn dead for five hours
 *  behind a timer that could not fire, and 229 rows stuck awaiting an embed while both health
 *  alerts pointed at delivery. The number existed every time; reading it meant knowing the query.
 *
 *  Polls rather than streams, deliberately: these counts move on a fifteen-minute tick, so a few
 *  seconds of staleness is invisible and a websocket would be a second delivery mechanism to keep
 *  alive for no gain the reader could see. */
function PipelineTab() {
  const [report, setReport] = useState<PipelineReport | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () =>
      api.get<PipelineReport>("/admin/pipeline")
        .then((data) => { if (live) { setReport(data); setFailed(false); } })
        .catch(() => { if (live) setFailed(true); });
    void load();
    // Five seconds is well inside the tick, so the page is never meaningfully behind, and the query
    // is a handful of indexed counts rather than anything that scans.
    const timer = setInterval(() => void load(), 5000);
    return () => { live = false; clearInterval(timer); };
  }, []);

  if (failed && !report) return <p className="muted">Could not read the pipeline.</p>;
  if (!report) return <p className="muted">Reading the pipeline…</p>;

  const busiest = Math.max(1, ...report.stages.map((s) => s.waiting));

  return (
    <>
      <h2>Where the jobs are</h2>
      <p className="hint" style={{ maxWidth: "60ch" }}>
        One row per stage, in the order a posting moves through them. <strong>Waiting</strong> is how
        many are sitting in that stage now; <strong>entered</strong> is how many arrived in the last{" "}
        {report.window_hours} hours. A stage turns amber after {report.amber_after_min} minutes of
        the oldest row waiting and red after {report.red_after_min} — the tick that should have
        cleared it runs every {report.amber_after_min / 2} minutes.
      </p>

      <div className="pipeline">
        {report.stages.map((stage) => (
          <div key={stage.key} className={`pipeline-row ${stage.verdict}`}>
            <div className="pipeline-head">
              <strong>{stage.label}</strong>
              <span className="small muted">{stage.what}</span>
            </div>
            <div className="pipeline-bar-wrap">
              {/* Width is relative to the busiest stage, so the shape shows where work piles up
                  rather than implying an absolute scale nobody set. */}
              <div className="pipeline-bar"
                   style={{ width: `${Math.round((stage.waiting / busiest) * 100)}%` }} />
            </div>
            <div className="pipeline-numbers">
              <span className="pipeline-waiting">{stage.waiting.toLocaleString()}</span>
              <span className="small muted">waiting</span>
              <span className="pipeline-entered">
                {stage.entered === null ? "—" : stage.entered.toLocaleString()}
              </span>
              <span className="small muted">in {report.window_hours}h</span>
              <span className="small muted">
                {stage.oldest_min === null ? "—" : `oldest ${minutes(stage.oldest_min)}`}
              </span>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function LatencyTab() {
  const [report, setReport] = useState<LatencyReport | null>(null);
  const [hours, setHours] = useState(24);
  // One row open at a time. Two open drill-downs is a second table, and the reason to open one is
  // to look at it rather than to compare it with another.
  const [open, setOpen] = useState<string | null>(null);
  // Kept per stage after the first fetch, so collapsing and reopening a row is instant and does not
  // re-run three queries to show what is already on the page.
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
                {/* A stage with a healthy p90 and a forty-minute tail has something worth looking
                    at that no percentile would ever show. */}
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

      {/* **"Not enough data" is a real answer, not a failure.** Below the sample floor a percentile
          is arithmetic rather than evidence, and colouring it invites somebody to chase a number
          that three rows produced. */}
      <p className="hint" style={{ marginTop: "0.9rem" }}>
        Every row is judged on its p90, not its median — half the jobs arriving promptly is not the
        claim this product makes, and a median hides exactly the tail somebody loses a role in. A
        row with too few completed examples says so rather than showing a colour.
      </p>
    </>
  );
}

/** The zone, expanded: waiting versus running, what is queued behind it, and the last day's shape.
 *
 *  These are not more percentiles. Waiting and running fail for different reasons and are fixed in
 *  different places; a backlog predicts the next hour where a percentile only describes the last
 *  one; and a snapshot cannot show a spike, which is the hour worth chasing. */
function StageDrill({ stage, detail }: { stage: StageRow; detail: StageDetail | undefined }) {
  if (!detail) return <p className="facts">Reading this stage…</p>;

  return (
    <div className="drill-body">
      <p className="drill-what">{stage.what}</p>
      {stage.caveat ? <p className="hint drill-caveat">{stage.caveat}</p> : null}

      <div className="stat-row">
        {/* **Waiting and running are two different failures.** Too few workers is not the same
            problem as work that got slower, and one "how long did it take" hides which. */}
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

/** One number with the sentence that says what to do about it. A tile whose meaning lives in a
 *  runbook is a tile an operator has to already understand to use. */
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

/**
 * The stage's p90 per hour, against its own thresholds.
 *
 * **A snapshot cannot show a spike**, and the spike is the hour worth chasing: a p90 of 36 minutes
 * averaged over a day hides the hour it was four times that.
 *
 * Deliberately one series in one ink colour, with the thresholds as labelled dashed rules. A bar
 * that has crossed the line is read by *position against a labelled line* rather than by turning a
 * colour — which is the same rule the verdict pills follow, and the only version that survives
 * being printed or read by somebody who cannot separate the two hues.
 */
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
            {/* The hover layer an SVG chart owes the reader. `title` rather than a floating div:
                one element, no positioning maths, and it survives keyboard focus. */}
            <title>
              {hourLabel(point.at)} · p90 {minutes(point.p90)} · median {minutes(point.p50)} ·{" "}
              {point.samples.toLocaleString()} jobs
            </title>
          </path>
        );
      })}

      {/* One direct label, on the worst hour. A number on every bar is eighteen numbers nobody
          reads; the peak is the only one anybody came here for. */}
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

/** A bar with its top corners rounded and its base square on the axis. `rx` on a rect would round
 *  the end sitting on the baseline too, which reads as the bar floating. */
function bar(x: number, y: number, width: number, height: number, radius: number): string {
  const r = Math.min(radius, width / 2, Math.max(height, 0));
  const base = y + Math.max(height, 0);
  return `M${x},${base} L${x},${y + r} Q${x},${y} ${x + r},${y} `
       + `L${x + width - r},${y} Q${x + width},${y} ${x + width},${y + r} `
       + `L${x + width},${base} Z`;
}

/** The disclosure arrow. A character rather than an inline SVG: the SVG rendered as a grey block in
 *  the row and flickered on every re-render as a 24-unit viewBox was re-rasterised to 12 pixels. */
function Caret({ open }: { open: boolean }) {
  return (
    <span className={open ? "caret-icon open" : "caret-icon"} aria-hidden="true">›</span>
  );
}

/** Minutes, in the unit a person would say them in. "94m" is a number to convert; "1h 34m" is a
 *  number to react to, and this table exists to be reacted to. */
function minutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return "<1m";
  if (value < 90) return `${Math.round(value)}m`;
  const whole = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return rest ? `${whole}h ${rest}m` : `${whole}h`;
}

/** Lane timings arrive in seconds — the work inside a lane is sub-minute when it is healthy, and
 *  rounding it to minutes would report every well-behaved lane as zero. */
function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return "<1s";
  if (value < 90) return `${Math.round(value)}s`;
  return minutes(value / 60);
}

function hourLabel(at: string): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Colour is never the only signal, so every pill carries its word — and an unjudged stage says
 *  that it was not judged rather than borrowing "not enough data", which would make a deliberate
 *  decision look like missing data. */
function verdictWord(stage: StageRow): string {
  if (!stage.judged) return "reported, not judged";
  if (stage.verdict === "ok") return "healthy";
  if (stage.verdict === "warn") return `slow, over ${stage.amber_after_min}m`;
  if (stage.verdict === "down") return `too slow, over ${stage.red_after_min}m`;
  return "not enough data";
}

/** The result ceiling for one source, or nothing where the source has no such control — Working
 *  Nomads returns its whole list and TheMuse counts pages, so a number there would be a lever
 *  attached to nothing. */
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

/** How deep a paginated source walks in one cycle. A dash where the source fetches one response —
 *  the same rule as `Results`: no control where there is nothing to control. */
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

/** How many terms a page shows. Two columns of ten: the three-column grid put twenty-odd terms on
 *  screen with no order anybody could see, which reads as a wall rather than a list. */
const TERMS_PER_PAGE = 20;

/** Previous/next for a list held in the browser. Renders nothing when everything already fits —
 *  a pager under a list of six is furniture. */
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
