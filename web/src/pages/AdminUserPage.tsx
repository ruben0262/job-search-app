/**
 * One account, for an operator.
 *
 * **Its own page, at its own address.** This started as a panel that unfolded inside the user list,
 * which meant no link to send somebody, no back button, and the thing you opened rendered
 * underneath the table you opened it from.
 *
 * The two questions an operator actually has are "why is this person's feed bad" and "did their
 * setup finish", so those are the two things above the fold. Setup completeness is computed by the
 * same predicate the trainer refuses on, which is what stops "their setup looks fine but training
 * is refused" from being possible without this page saying why.
 *
 * Read-only, and no credential material — not even `last_4`. An operator does not need it to answer
 * either question, and a support view showing a fragment of a key is one that leaks it over
 * somebody's shoulder.
 */
import { useEffect, useState } from "react";

import { api } from "../api";

type Span = { key: string; label: string; value: string | number };

type UserDetail = {
  user: {
    id: string;
    email: string;
    status: string;
    active: boolean;
    plan: string;
    telegram: string;
    profile_generation: number;
    model_version: number | null;
    onboarded: boolean;
    created_at: string | null;
    last_active_at: string | null;
  };
  setup: { missing: string[]; files: { name: string; kind: string; filled: boolean }[] };
  models: {
    version: number;
    active: boolean;
    n_labels: number | null;
    trained_at: string | null;
    metrics: Span[];
  }[];
  counts: { labels: number; scored: number; applied: number; notified: number };
};

export function AdminUserPage({ userId }: { userId: string }) {
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    api
      .get<UserDetail>(`/admin/users/${userId}`)
      .then(setDetail)
      .catch(() => setFailed(true));
  }, [userId]);

  if (failed) return <p className="error">Could not read that account.</p>;
  if (!detail) return <p className="muted">Loading…</p>;

  const { user, setup, models, counts } = detail;
  const columns = models[0]?.metrics ?? [];

  return (
    <div className="page-in">
      <p className="kicker">Operator</p>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 style={{ marginBottom: 0 }}>{user.email}</h1>
        <a href="#/admin">← All accounts</a>
      </div>
      <p className="facts">
        {user.status}
        {user.active ? "" : " · delivery paused"} · telegram {user.telegram} · plan {user.plan} ·
        profile generation {user.profile_generation}
        {user.last_active_at
          ? ` · last active ${new Date(user.last_active_at).toLocaleDateString()}`
          : ""}
      </p>

      <dl className="stats">
        <div className="stat">
          <dt>{counts.labels.toLocaleString()}</dt>
          <dd>labels</dd>
        </div>
        <div className="stat">
          <dt>{counts.scored.toLocaleString()}</dt>
          <dd>jobs scored</dd>
        </div>
        <div className="stat">
          <dt>{counts.applied.toLocaleString()}</dt>
          <dd>applied</dd>
        </div>
        <div className="stat">
          <dt>{counts.notified.toLocaleString()}</dt>
          <dd>delivered</dd>
        </div>
      </dl>

      <h2>Setup</h2>
      {setup.missing.length ? (
        <p className="error">
          Incomplete — {setup.missing.length} file(s) missing or unfilled:{" "}
          {setup.missing.join(", ")}. Training is refused until these exist.
        </p>
      ) : (
        <p className="facts" style={{ color: "var(--score)" }}>
          Complete — every required profile file is written.
        </p>
      )}
      <div className="sources" style={{ marginBottom: "2rem" }}>
        {setup.files.map((file) => (
          <div key={file.name} className="source-row">
            <span className="source-name">{file.name}</span>
            <span className="small" style={{ color: file.filled ? "var(--score)" : "var(--dim)" }}>
              {file.filled ? "written" : "empty"}
            </span>
          </div>
        ))}
      </div>

      <h2>Model history</h2>
      {models[0] ? (
        <div className="table-scroll">
          <table className="history">
            <thead>
              <tr>
                <th>Version</th>
                <th>Trained</th>
                <th>Labels</th>
                {columns.map((metric) => (
                  <th key={metric.key}>{metric.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {models.map((entry) => (
                <tr key={entry.version} className={entry.active ? "on" : undefined}>
                  <td>
                    v{entry.version}
                    {entry.active ? <span className="pill">live</span> : null}
                  </td>
                  <td>
                    {entry.trained_at ? new Date(entry.trained_at).toLocaleDateString() : "—"}
                  </td>
                  <td>{entry.n_labels?.toLocaleString() ?? "—"}</td>
                  {columns.map((column) => {
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
  );
}
