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
import { useEffect, useState } from "react";

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

type Settings = {
  delivery_interval_min: number;
  source_freshness_hours: Record<string, number>;
  default_freshness_hours: number;
};

type Source = { name: string; hours: number };


/** The four questions an operator arrives with, in the order they ask them. */
const TABS: [Tab, string][] = [
  ["health", "Health"],
  ["settings", "Settings"],
  ["corpus", "Corpus"],
  ["latency", "Latency"],
  ["accounts", "Accounts"],
];

type Tab = "health" | "settings" | "corpus" | "latency" | "accounts";

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
  // The account being deleted, held until the typed address matches. `null` closes the dialog.
  const [doomed, setDoomed] = useState<AdminUser | null>(null);
  const [typed, setTyped] = useState("");
  const [tab, setTab] = useState<Tab>("health");
  const [failed, setFailed] = useState(false);

  // One source, one number. Written as a whole map because that is what the setting is — reading
  // it back from the response keeps the panel honest about what was actually stored.
  const saveFreshness = useAction(async (name: string, hours: number) => {
    const next = { ...(settings?.source_freshness_hours ?? {}), [name]: hours };
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/source_freshness_hours", { value: next });
    setSettings(body.settings);
    setSources((rows) => rows?.map((row) => (row.name === name ? { ...row, hours } : row)) ?? rows);
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
      api.get<{ sources: Source[] }>("/admin/sources"),
    ])
      .then(([h, u, s, src]) => {
        setHealth(h);
        setUsers(u.users);
        setSettings(s.settings);
        setSources(src.sources);
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
              <strong>{alert.severity === "page" ? "Needs you now" : "Needs you today"}</strong>
              {alert.user_email ? <span className="muted"> · {alert.user_email}</span> : null}
              <br />
              {alert.text}
              <br />
              <span className="small muted">{alert.action}</span>
            </div>
          ))}
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
            {[15, 30, 60].map((minutes) => (
              <option key={minutes} value={minutes}>every {minutes} minutes</option>
            ))}
          </select>
          <Error>{saveCadence.error}</Error>

          <p className="label" style={{ marginTop: "1.5rem" }}>Job freshness, per source</p>
          <span className="hint">
            How recent a posting must be to qualify, in hours. LinkedIn lists within minutes; a
            slower board given LinkedIn&apos;s window would never qualify at all. Anything left at
            the default runs at {settings.default_freshness_hours}h.
          </span>
          <Error>{saveFreshness.error}</Error>
          <div className="sources">
            {(sources ?? []).map((source) => (
              <label key={source.name} className="source-row">
                <span className="source-name">{source.name}</span>
                <select
                  value={source.hours}
                  disabled={saveFreshness.busy}
                  onChange={(event) =>
                    void saveFreshness.run(source.name, Number(event.target.value))}
                >
                  {[1, 2, 4, 6, 12, 24, 48, 72].map((hours) => (
                    <option key={hours} value={hours}>{hours}h</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        </div>
      ) : null}</> : null}


      {tab === "corpus" ? <CorpusTab /> : null}
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
