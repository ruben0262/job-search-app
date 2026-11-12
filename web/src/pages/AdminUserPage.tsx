/** One account, for an operator. */
import { Fragment, useState } from "react";

import { ApiError, api } from "../api";
import { useKept } from "../kept";
import { money, standingWord } from "../membership";
import { day } from "../time";
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

/** How long ago: "just now", "12 min ago", "4 hours ago", "7 days ago", or "never". */
function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  return minutes < 1 ? "just now" : minutes < 60 ? `${minutes} min ago`
    : minutes < 48 * 60 ? `${Math.round(minutes / 60)} hours ago` : `${Math.round(minutes / 1440)} days ago`;
}

/** A date and the time of day: "Tue 4 Nov 2026, 14:32". */
function when(iso: string): string {
  return new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", year: "numeric",
                                            hour: "2-digit", minute: "2-digit" });
}

/** "1 CV", "2 CVs". */
function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

/** Plain chips, a word each, or "none". */
function chips(values: string[], className: string) {
  return values.length ? values.map((value) => <span key={value} className={className}>{value}</span>)
    : <span className="muted">none</span>;
}

/** Where an account stands, in a word and a colour (`.verdict`). */
const STATUS: Record<string, [string, string]> = {
  active: ["Active", "ok"],
  pending_verification: ["Email not confirmed", "warn"],
  suspended: ["Suspended", "down"],
  deleted: ["Deleted", "down"],
};
const TELEGRAM: Record<string, string> = {
  connected: "Connected", waiting: "Waiting for them to press Start", broken: "Broken: their bot stopped answering",
  unconfigured: "Not set up",
};

export function AdminUserPage({ userId }: { userId: string }) {
  // Drawn from what was read last time (or read ahead from the list) at once, and kept current.
  const account = useKept<UserDetail>(`/admin/users/${userId}`);
  const catalogue = useKept<{ limits: LimitSpec[] }>("/admin/limits");
  const detail = account.data;
  const specs = catalogue.data?.limits ?? [];
  const setDetail = (change: (current: UserDetail | null) => UserDetail | null) => account.set(change);
  const [saving, setSaving] = useState<string | null>(null);
  const failed = account.failed && !detail;

  // Over whatever Stripe says, and Stripe charges nothing while it is on (`/admin/users/{id}/free-access`): the
  // switch moves only once Stripe has, and says why when it could not.
  const [freeError, setFreeError] = useState<string | null>(null);
  const toggleFree = async () => {
    setSaving("free_access");
    setFreeError(null);
    try {
      await api.put(`/admin/users/${userId}/free-access`, { on: !detail?.user.free_access_at });
      await account.reload();
    } catch (caught) {
      setFreeError(caught instanceof ApiError ? caught.detail : "That didn't work. Try again.");
    } finally {
      setSaving(null);
    }
  };

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
  const reached = usage.applications;
  const [status, tone] = STATUS[user.status] ?? [user.status, "unknown"];
  const [membership, paying] = standingWord(user.subscription_status, Boolean(user.free_access_at), user.is_admin);
  const ended = user.subscription_status === "canceled" || user.subscription_status === "incomplete_expired";
  const renews = !user.cancels_at && (user.subscription_status === "trialing" || user.subscription_status === "active");
  const most = Math.max(1, ...usage.sources.map((each) => each.jobs));
  // What they did, newest first: only what has happened.
  const story = ([
    ["On the site", user.last_active_at], ["Sent an alert", usage.last_alert_at],
    ["Made a CV or cover letter", usage.last_made_at], ["Applied to a job", usage.last_applied_at],
    ["Finished setting up", user.onboarding_completed_at], ["Confirmed their email", user.email_verified_at],
    ["Signed up", user.created_at],
  ] as const).flatMap(([what, at]) => (at ? [{ what, at }] : [])).sort((a, b) => b.at.localeCompare(a.at));

  return (
    <div className="account">
      <a className="back" href="#/admin/accounts">← All accounts</a>
      <header className="account-head">
        <span className="account-mark" aria-hidden="true">{(user.full_name || user.email).charAt(0)}</span>
        <div className="account-who">
          <h1>{user.full_name || user.email}</h1>
          {user.full_name ? <p className="muted">{user.email}</p> : null}
          <ul className="account-badges" aria-label="Where the account stands">
            <li className={`verdict ${tone}`}>{status}</li>
            <li className={`verdict ${user.member ? paying : "down"}`}>{membership}</li>
            {user.onboarded ? null : <li className="verdict warn">Still setting up</li>}
            {user.active ? null : <li className="verdict warn">Alerts paused</li>}
            {user.is_admin ? <li className="verdict unknown">Admin</li> : null}
          </ul>
        </div>
        <p className="account-seen">
          Last here <strong>{ago(user.last_active_at)}</strong>
          <span>Joined {ago(user.created_at)}</span>
        </p>
      </header>

      <dl className="account-tiles">
        <div>
          <dt>On their list</dt>
          <dd><strong>{usage.jobs.toLocaleString()}</strong>{usage.new_jobs.toLocaleString()} new since their last visit</dd>
        </div>
        <div>
          <dt>Sent by alerts</dt>
          <dd><strong>{usage.delivered.toLocaleString()}</strong>last {ago(usage.last_alert_at)}</dd>
        </div>
        <div>
          <dt>Made here</dt>
          <dd>
            <strong>{(usage.cvs_made + usage.covers_made).toLocaleString()}</strong>
            {plural(usage.cvs_made, "CV")} · {plural(usage.covers_made, "cover letter")}
          </dd>
        </div>
        <div>
          <dt>Skipped</dt>
          <dd><strong>{usage.skipped.toLocaleString()}</strong>gone from their list for good</dd>
        </div>
      </dl>

      <section className="card">
        <h2>Membership</h2>
        <dl className="facts-list">
          <dt>The site</dt>
          <dd>{user.member ? "Open to them" : <span className="verdict down">Closed: they see the page that asks them to pay</span>}</dd>
          {user.trial_ends_at ? (
            <>
              <dt>Free trial</dt>
              <dd>{user.subscription_status === "trialing" ? "until" : "ended"} {day(user.trial_ends_at)}</dd>
            </>
          ) : null}
          {renews && user.period_ends_at ? (
            <>
              <dt>Next payment</dt>
              <dd>{day(user.period_ends_at)}</dd>
            </>
          ) : null}
          {user.cancels_at ? (
            <>
              <dt>{ended ? "Ended" : "Ends, cancelled"}</dt>
              <dd>{day(user.cancels_at)}</dd>
            </>
          ) : null}
          <dt>Last payment</dt>
          <dd>
            {user.last_paid_at && user.last_paid_amount !== null && user.currency
              ? `${money(user.last_paid_amount, user.currency)} on ${day(user.last_paid_at)}` : "none yet"}
          </dd>
          <dt>Stripe customer</dt>
          <dd>{user.stripe_customer_id ?? <span className="muted">none yet</span>}</dd>
          {user.billing_synced_at ? (
            <>
              <dt>Read from Stripe</dt>
              <dd>{ago(user.billing_synced_at)}</dd>
            </>
          ) : null}
        </dl>
        <label className="switch" title="The whole site without a membership">
          <input type="checkbox" role="switch" checked={Boolean(user.free_access_at)}
                 disabled={saving === "free_access"} onChange={() => void toggleFree()} />
          <span>Free access{user.free_access_at ? `, since ${day(user.free_access_at)}` : ""}</span>
        </label>
        {freeError ? <p className="error">{freeError}</p> : null}
        <p className="muted small">
          The whole site, CVs, cover letters and alerts included, whatever Stripe says. A membership they pay for is held
          meanwhile: Stripe charges nothing while this is on, a trial runs out without a charge, and the payments
          start again from the next one when it is off.
        </p>
      </section>

      <section className="card">
        <h2>Their applications</h2>
        {/* Each stage counts every application that reached it or went past it, so they read left to right. */}
        <ol className="funnel">
          {([["Applied", reached.applied], ["Interview", reached.interview], ["Offer", reached.offer],
             ["Hired", reached.hired]] as const).map(([stage, count]) => (
            <li key={stage}>
              <strong>{count.toLocaleString()}</strong>
              <span>{stage}</span>
              <i style={{ inlineSize: `${reached.applied ? (count / reached.applied) * 100 : 0}%` }} />
            </li>
          ))}
        </ol>
        <p className="muted small">
          {reached.applied ? `${plural(reached.rejected, "rejection")} so far.` : "Nothing marked as applied yet."}
        </p>
      </section>

      <section className="card">
        <h2>What they look for</h2>
        <dl className="account-search">
          <dt>Green words</dt>
          <dd>
            {usage.green_words.length ? usage.green_words.map((word) => (
              <span key={word.name} className={word.jobs ? "word green" : "word green none"}
                    title={`${plural(word.jobs, "job")} on their list`}>
                {word.name}<b>{word.jobs.toLocaleString()}</b>
              </span>
            )) : <span className="muted">none</span>}
          </dd>
          <dt>Red words</dt>
          <dd>{chips(usage.red_words, "word red")}</dd>
          <dt>Countries</dt>
          <dd>
            {usage.countries.length ? usage.countries.map((place) => (
              <span key={place.name} className={place.jobs ? "word" : "word none"}
                    title={`${plural(place.jobs, "job")} on their list`}>
                {place.name}<b>{place.jobs.toLocaleString()}</b>
              </span>
            )) : <span className="muted">none</span>}
          </dd>
          <dt>How they work</dt>
          <dd>{chips(usage.workplaces, "word")}</dd>
          <dt>Languages</dt>
          <dd>{chips(usage.languages, "word")}</dd>
        </dl>
        <p className="muted small">The numbers are the jobs on their list each one accounts for.</p>
      </section>

      <section className="card">
        <h2>Where their jobs come from</h2>
        {usage.sources.length ? (
          <ul className="account-bars">
            {usage.sources.map((source) => (
              <li key={source.name}>
                <span>{source.name}</span>
                <i aria-hidden="true"><i style={{ inlineSize: `${(source.jobs / most) * 100}%` }} /></i>
                <strong>{source.jobs.toLocaleString()}</strong>
              </li>
            ))}
          </ul>
        ) : <p className="muted">Nothing on their list.</p>}
      </section>

      <div className="account-pair">
        <section className="card">
          <h2>Documents</h2>
          <dl className="facts-list">
            <dt>Their CV</dt>
            <dd>{plural(usage.cvs, "uploaded", "uploaded")}, {usage.cvs_in_use} in use</dd>
            <dt>Cover letters</dt>
            <dd>{plural(usage.cover_letters, "uploaded", "uploaded")}</dd>
            <dt>Looks</dt>
            <dd>{usage.cv_look} CV · {usage.cover_look} letter</dd>
            <dt>AI account</dt>
            <dd>
              {usage.ai_accounts.map((name) => (
                <div key={name}>{name} {name === usage.ai_in_use ? <span className="verdict ok">in use</span> : null}</div>
              ))}
              {usage.ai_in_use ? null : (
                <span className="verdict warn">{usage.ai_accounts.length ? "None in use" : "None linked"}</span>
              )}
            </dd>
          </dl>
        </section>
        <section className="card">
          <h2>Alerts</h2>
          <dl className="facts-list">
            {(["email", "browser"] as const).map((way) => (
              <Fragment key={way}>
                <dt>{way === "email" ? "Email" : "Browser"}</dt>
                <dd>{usage.alerts.includes(way) ? <span className="verdict ok">On</span>
                  : <span className="verdict unknown">Off</span>}</dd>
              </Fragment>
            ))}
            <dt>Telegram</dt>
            <dd>
              <span className={`verdict ${user.telegram === "connected" ? "ok" : user.telegram === "broken" ? "down"
                : "unknown"}`}>{TELEGRAM[user.telegram] ?? user.telegram}</span>
            </dd>
          </dl>
        </section>
      </div>

      <section className="card">
        <h2>What they did</h2>
        <ol className="timeline">
          {story.map(({ what, at }) => (
            <li key={what}>
              <strong>{what}</strong>
              <span>{when(at)} · {ago(at)}</span>
            </li>
          ))}
        </ol>
      </section>

      {/* Set here, and nowhere the user can reach. */}
      <section className="card">
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
      </section>
    </div>
  );
}
