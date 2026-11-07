/** One account, for an operator. */
import { useState } from "react";

import { api } from "../api";
import { useKept } from "../kept";
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

/** A date and the time of day, and how long ago: "Tue 4 Nov 2026, 14:32 (2 hours ago)". */
function when(iso: string | null | undefined): string {
  if (!iso) return "never";
  const at = new Date(iso);
  const minutes = Math.round((Date.now() - at.getTime()) / 60_000);
  const ago = minutes < 1 ? "just now" : minutes < 60 ? `${minutes} min ago`
    : minutes < 48 * 60 ? `${Math.round(minutes / 60)} hours ago` : `${Math.round(minutes / 1440)} days ago`;
  return `${at.toLocaleString([], { weekday: "short", day: "numeric", month: "short", year: "numeric",
                                    hour: "2-digit", minute: "2-digit" })} (${ago})`;
}

function list(values: string[]): string {
  return values.length ? values.join(", ") : "none";
}

export function AdminUserPage({ userId }: { userId: string }) {
  // Drawn from what was read last time (or read ahead from the list) at once, and kept current.
  const account = useKept<UserDetail>(`/admin/users/${userId}`);
  const catalogue = useKept<{ limits: LimitSpec[] }>("/admin/limits");
  const detail = account.data;
  const specs = catalogue.data?.limits ?? [];
  const setDetail = (change: (current: UserDetail | null) => UserDetail | null) => account.set(change);
  const [saving, setSaving] = useState<string | null>(null);
  const failed = account.failed && !detail;

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

  if (failed) return <p className="error">Could not read that account.</p>;
  if (!detail) return <p className="muted">Loading…</p>;

  const { user, usage } = detail;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 style={{ marginBottom: 0 }}>{user.email}</h1>
        <a href="#/admin">← All accounts</a>
      </div>
      <p className="facts">
        {user.status}
        {user.active ? "" : " · delivery paused"} · last online {when(user.last_active_at)}
      </p>

      <dl className="stats">
        {([[usage.jobs, "jobs waiting"], [usage.new_jobs, "new since last here"], [usage.applied, "applied"],
           [usage.skipped, "skipped"], [usage.made, "CVs and letters made"],
           [usage.delivered, "sent by alerts"]] as const).map(([value, label]) => (
          <div key={label} className="stat">
            <dt>{value.toLocaleString()}</dt>
            <dd>{label}</dd>
          </div>
        ))}
      </dl>

      {/* What they told us and how they use the site, for working out what to improve. */}
      <h2>Their account</h2>
      <dl className="facts-list">
        <dt>Signed up</dt>
        <dd>{when(user.created_at)}</dd>
        <dt>Finished setting up</dt>
        <dd>{user.onboarding_completed_at ? when(user.onboarding_completed_at) : "not yet"}</dd>
        <dt>Green words</dt>
        <dd>{list(usage.green_words)}</dd>
        <dt>Red words</dt>
        <dd>{list(usage.red_words)}</dd>
        <dt>Countries</dt>
        <dd>{list(usage.countries)}</dd>
        <dt>How they work</dt>
        <dd>{list(usage.workplaces)}</dd>
        <dt>Languages</dt>
        <dd>{list(usage.languages)}</dd>
        <dt>Uploaded</dt>
        <dd>
          {usage.cvs} CV{usage.cvs === 1 ? "" : "s"} ({usage.cvs_in_use} in use), {usage.cover_letters} cover
          letter{usage.cover_letters === 1 ? "" : "s"}
        </dd>
        <dt>Alerts</dt>
        <dd>{usage.alerts.length ? usage.alerts.join(", ") : "none"} · Telegram {user.telegram}</dd>
      </dl>

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
