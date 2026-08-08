/** One account, for an operator. */
import { useEffect, useState } from "react";

import { api } from "../api";

type Span = { key: string; label: string; value: string | number };

type LimitSpec = {
  key: string;
  label: string;
  default: number | null;
  unit: string;
  detail: string;
  allow_unlimited: boolean;
  /** The settable values, from `libs.limits`. Not a copy kept here — see the note on the render. */
  choices: number[];
};

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
    profile_files_visible: boolean;
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
  /** `null` is unlimited and is a real value, not a missing one. */
  limits: Record<string, number | null>;
};

export function AdminUserPage({ userId }: { userId: string }) {
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [specs, setSpecs] = useState<LimitSpec[]>([]);
  const [saving, setSaving] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get<UserDetail>(`/admin/users/${userId}`),
      api.get<{ limits: LimitSpec[] }>("/admin/limits"),
    ])
      .then(([body, catalogue]) => {
        setDetail(body);
        setSpecs(catalogue.limits);
      })
      .catch(() => setFailed(true));
  }, [userId]);

  // The catalogue drives the form, so adding a ceiling is one entry in `libs.limits` rather than an entry.
  const save = async (key: string, raw: string) => {
    setSaving(key);
    try {
      const body = await api.put<{ limits: Record<string, number | null> }>(
        `/admin/users/${userId}/limits/${key}`,
        { value: raw === "unlimited" ? null : Number(raw) },
      );
      setDetail((current) => (current ? { ...current, limits: body.limits } : current));
    } finally {
      setSaving(null);
    }
  };

  const setFilesVisible = async (visible: boolean) => {
    setSaving("files");
    try {
      await api.put(`/admin/users/${userId}/files`, { visible });
      setDetail((current) =>
        current ? { ...current, user: { ...current.user, profile_files_visible: visible } } : current);
    } finally {
      setSaving(null);
    }
  };

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

      {/* Per account, and closed until somebody opens it. */}
      <h2>Profile files</h2>
      <label className="pick">
        <input
          type="checkbox"
          checked={user.profile_files_visible === true}
          disabled={saving === "files"}
          onChange={(event) => void setFilesVisible(event.target.checked)}
        />
        <span>
          {user.profile_files_visible
            ? "Open, this user can read and correct the files written about them"
            : "Closed, the tiles are shown but cannot be opened"}
        </span>
      </label>

      <h2>Setup</h2>
      {setup.missing.length ? (
        <p className="error">
          Incomplete, {setup.missing.length} file(s) missing or unfilled:{" "}
          {setup.missing.join(", ")}. Training is refused until these exist.
        </p>
      ) : (
        <p className="facts" style={{ color: "var(--score)" }}>
          Complete, every required profile file is written.
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

      {/* Set here, and nowhere the user can reach. */}
      <h2>Limits</h2>
      <p className="facts">
        Blank means the platform default. Sending is paced at the gap below, so an unlimited daily
        ceiling drains a backlog slowly rather than getting the bot restricted.
      </p>
      <div className="limits">
        {specs.map((spec) => {
          const current = detail.limits[spec.key];
          return (
            <label key={spec.key} className="limit-row">
              <span className="limit-label">
                {spec.label}
                <span className="hint">{spec.detail}</span>
              </span>
              <select
                value={current === null ? "unlimited" : String(current)}
                disabled={saving === spec.key}
                onChange={(event) => void save(spec.key, event.target.value)}
              >
                {/* From the catalogue, never a list kept here. A ceiling whose options lived in
                    this file rendered an empty dropdown when it was added to `libs.limits` and
                    nobody remembered this map — a control that exists, is wired end to end, and
                    cannot be operated. */}
                {spec.choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice} {spec.unit}
                  </option>
                ))}
                {/* Only where the ceiling allows it. */}
                {spec.allow_unlimited ? <option value="unlimited">Unlimited</option> : null}
              </select>
            </label>
          );
        })}
      </div>

      <h2 style={{ marginTop: "2.5rem" }}>Model history</h2>
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
