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
import { useAction } from "../ui";

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
};

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

export function AdminPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [failed, setFailed] = useState(false);

  const openUser = useAction(async (id: string) => {
    setDetail(await api.get<UserDetail>(`/admin/users/${id}`));
  });

  useEffect(() => {
    Promise.all([
      api.get<Health>("/admin/health"),
      api.get<{ users: AdminUser[] }>("/admin/users"),
    ])
      .then(([h, u]) => {
        setHealth(h);
        setUsers(u.users);
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
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>
                {/* An operator's two questions -- "why is this person's feed bad" and "did their
                    setup finish" -- are both one page down, and neither is answerable from this
                    row. */}
                <button type="button" className="linklike" onClick={() => void openUser.run(user.id)}>
                  {user.email}
                </button>
              </td>
              <td className="job-sub">
                {user.status}
                {user.active ? "" : " · paused"}
              </td>
              <td className="job-sub">{user.telegram}</td>
              <td className="num job-sub">{user.model_version ? `v${user.model_version}` : "—"}</td>
              <td className="job-sub">{user.onboarded ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="facts" style={{ marginTop: "2rem" }}>
        Read-only. Suspending, requeueing and resending are still `deploy/scripts/` over SSH — see
        `OPERATIONS_PLAN.md` §5 for the console that acts, and why it has not earned its build at
        this size yet.
      </p>
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
