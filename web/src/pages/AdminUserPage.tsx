/** One account, for an operator. */
import { useEffect, useState } from "react";

import { api } from "../api";
import type { components } from "../api-types";

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

type UserDetail = components["schemas"]["AdminAccountDetail"];

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

  const { user, setup, counts } = detail;

  return (
    <div className="page-in">
      <p className="kicker">Operator</p>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 style={{ marginBottom: 0 }}>{user.email}</h1>
        <a href="#/admin">← All accounts</a>
      </div>
      <p className="facts">
        {user.status}
        {user.active ? "" : " · delivery paused"} · telegram {user.telegram}
        {user.last_active_at
          ? ` · last active ${new Date(user.last_active_at).toLocaleDateString()}`
          : ""}
      </p>

      <dl className="stats">
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
          Incomplete, {setup.missing.length} file(s) missing or unfilled:{" "}
          {setup.missing.join(", ")}.
        </p>
      ) : (
        <p className="facts" style={{ color: "var(--score)" }}>
          Complete, every required profile file is written.
        </p>
      )}
      <div className="sources">
        {setup.files.map((file) => (
          <div key={file.name} className="source-row">
            <span className="source-name">{file.name}</span>
            <span className="small" style={{ color: file.filled ? "var(--score)" : "var(--dim)" }}>
              {file.filled ? "written" : "empty"}
            </span>
          </div>
        ))}
      </div>
      {/* Whether the user may open the files listed directly above. Per account, and closed until
          somebody opens it — it was its own heading further up, with nothing else under it. */}
      <label className="pick" style={{ marginBottom: "2rem" }}>
        <input
          type="checkbox"
          role="switch"
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
    </div>
  );
}
