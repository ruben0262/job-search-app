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

type Span = { p50: number | null; p90: number | null; p99: number | null; samples: number };

type Settings = {
  delivery_interval_min: number;
  source_freshness_hours: Record<string, number>;
  default_freshness_hours: number;
};

type Source = { name: string; hours: number };

type UserDetail = {
  user: AdminUser & { created_at: string | null; last_active_at: string | null;
                      profile_generation: number };
  setup: { missing: string[]; files: { name: string; kind: string; filled: boolean }[] };
  models: {
    version: number;
    active: boolean;
    n_labels: number | null;
    trained_at: string | null;
    metrics: { key: string; label: string; value: string | number }[];
  }[];
  counts: { labels: number; scored: number; applied: number; notified: number };
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
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [sources, setSources] = useState<Source[] | null>(null);
  // The account being deleted, held until the typed address matches. `null` closes the dialog.
  const [doomed, setDoomed] = useState<AdminUser | null>(null);
  const [typed, setTyped] = useState("");
  const [failed, setFailed] = useState(false);

  // Freshness is edited as JSON rather than a row per source: the set of sources changes, and a
  // form that has to be rebuilt every time one is added is a form that goes stale.
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
    setDetail(null);
  });

  const saveCadence = useAction(async (minutes: number) => {
    const body = await api.put<{ settings: Settings }>(
      "/admin/settings/delivery_interval_min", { value: minutes });
    setSettings(body.settings);
  });

  const openUser = useAction(async (id: string) => {
    setDetail(await api.get<UserDetail>(`/admin/users/${id}`));
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

      <p className="kicker">Queue</p>
      {/* **What the operator owns.** Freshness and cadence stopped being user preferences on
          2026-08-01: the product streams the newest postings, so both are properties of what it is
          rather than choices. Per source, because sites differ in how fast they list. */}
      {settings ? (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ marginTop: 0 }}>Platform settings</h2>

          <label className="label" htmlFor="cadence">Delivery cadence</label>
          <span className="hint">
            How often every user&apos;s feed is sent. The same for everyone — a longer cadence
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
      ) : null}

      {/* **The claim this product makes, measured.** A four-hour freshness window means little if
          the pipeline spends two of them. Percentiles rather than an average: the tail is what a
          user experiences on the day their job was late. */}
      {health.latency ? (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ marginTop: 0 }}>Pipeline latency</h2>
          <p className="facts">
            Minutes, over the last {health.latency.window_hours} hours.
          </p>
          <div className="table-scroll">
            <table className="history">
              <thead>
                <tr>
                  <th>Stage</th><th>Median</th><th>p90</th><th>p99</th><th>Samples</th>
                </tr>
              </thead>
              <tbody>
                {([
                  ["Found → scored", health.latency.found_to_scored],
                  ["Scored → delivered", health.latency.scored_to_delivered],
                  ["Found → delivered", health.latency.found_to_delivered],
                ] as [string, Span][]).map(([label, span]) => (
                  <tr key={label} className={label.startsWith("Found → d") ? "on" : undefined}>
                    <td>{label}</td>
                    <td>{span.p50 ?? "—"}</td>
                    <td>{span.p90 ?? "—"}</td>
                    <td>{span.p99 ?? "—"}</td>
                    <td>{span.samples.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {detail ? (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ margin: 0 }}>{detail.user.email}</h2>
            <button type="button" className="secondary small-btn" onClick={() => setDetail(null)}>
              Close
            </button>
          </div>
          <p className="facts">
            {detail.user.status}
            {detail.user.active ? "" : " · paused"} · telegram {detail.user.telegram} · profile
            generation {detail.user.profile_generation} ·{" "}
            {detail.counts.labels.toLocaleString()} labels ·{" "}
            {detail.counts.scored.toLocaleString()} scored ·{" "}
            {detail.counts.applied.toLocaleString()} applied ·{" "}
            {detail.counts.notified.toLocaleString()} delivered
          </p>

          {/* Setup completeness by the same predicate the trainer refuses on, so "their setup looks
              finished but training is refused" cannot happen without this saying why. */}
          <p className={detail.setup.missing.length ? "error" : "facts"}>
            {detail.setup.missing.length
              ? `Setup incomplete — missing or unfilled: ${detail.setup.missing.join(", ")}. Training is refused until these exist.`
              : "Setup complete — every required profile file is written."}
          </p>

          {detail.models[0] ? (
            <div className="table-scroll">
              <table className="history">
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Trained</th>
                    <th>Labels</th>
                    {detail.models[0].metrics.map((metric) => (
                      <th key={metric.key}>{metric.label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {detail.models.map((entry) => (
                    <tr key={entry.version} className={entry.active ? "on" : undefined}>
                      <td>
                        v{entry.version}
                        {entry.active ? <span className="pill">live</span> : null}
                      </td>
                      <td>{entry.trained_at ? new Date(entry.trained_at).toLocaleDateString() : "—"}</td>
                      <td>{entry.n_labels?.toLocaleString() ?? "—"}</td>
                      {detail.models[0]!.metrics.map((column) => {
                        const cell = entry.metrics.find((metric) => metric.key === column.key);
                        return <td key={column.key}>{cell ? String(cell.value) : "—"}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="facts">No model has ever been trained for this account.</p>
          )}
        </div>
      ) : null}

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

      <p className="kicker" style={{ marginTop: "2.5rem" }}>
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
                  navigate — and a delete that opens a page underneath its own dialog is worse. */}
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
