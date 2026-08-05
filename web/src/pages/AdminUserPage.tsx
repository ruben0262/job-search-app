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

type LimitSpec = {
  key: string;
  label: string;
  default: number | null;
  unit: string;
  detail: string;
  allow_unlimited: boolean;
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

/** The values an operator picks from, plus Unlimited where the ceiling allows it. Coarse on
 *  purpose: these are judgements about a person's account, not a tuning dial. */
const CHOICES: Record<string, number[]> = {
  notifications_per_day: [25, 50, 100, 250, 500, 1000],
  items_per_run: [5, 10, 15, 20, 30, 50],
  send_gap_seconds: [3, 4, 5, 8, 12, 20],
  proxy_mb_per_day: [100, 200, 500, 1000, 2000],
  // LinkedIn stops counting at 200 and shows "Over 200 applicants", so 200 is the top real step —
  // anything above it would be a ceiling no posting can reach. Unlimited is the way to turn the
  // drop off entirely.
  applicant_cap: [25, 50, 100, 150, 200],
  linkedin_interval_min: [15, 30, 60, 120],
  describe_per_run: [20, 40, 60, 100, 200],
  keywords: [10, 25, 50, 100],
  documents: [10, 25, 50, 100],
  conversations: [1, 3, 5, 10],
  cv_builds_per_day: [5, 10, 20, 50, 100],
  label_pool: [100, 300, 500, 1000],
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

  // The catalogue drives the form, so adding a ceiling is one entry in `libs.limits` rather than
  // an entry plus a field plus a label somebody has to keep in step.
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

      {/* **Per account, and closed until somebody opens it.** These files are an assessment of a
          person written by an agent and reviewed by nobody, so this is a decision about what one
          particular set of them currently says. The tiles stay on their page either way; what this
          controls is whether opening one works, enforced on the read and not only in the page. */}
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

      {/* **Set here, and nowhere the user can reach.** Somebody who could raise their own delivery
          ceiling could get their own Telegram bot rate-limited by Telegram, and the rest are spend.
          Unlimited is a real value, stored as null, and is safe on delivery because sending is
          paced — the cap used to be the only guard against rate limits, and it guarded them by
          withholding jobs the user had matched. */}
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
                {(CHOICES[spec.key] ?? []).map((choice) => (
                  <option key={choice} value={choice}>
                    {choice} {spec.unit}
                  </option>
                ))}
                {/* Only where the ceiling allows it. `send_gap_seconds` never does: that one is
                    Telegram's rule rather than ours. */}
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
