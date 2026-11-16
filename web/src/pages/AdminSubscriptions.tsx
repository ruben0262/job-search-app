/** Admin → Subscriptions (owner, 2026-11-12): who is on which plan, what is about to change, what the membership
 * brings in, the membership itself (Stripe's price, its free days, how far behind the free plan is), what the
 * landing page says about the two plans, and every subscription as it is stored. */
import { useState } from "react";

import { api, type Schema } from "../api";
import { useKept } from "../kept";
import { type Pricing, saving } from "../landing/Landing";
import { money, price } from "../membership";
import { day } from "../time";
import { Error, useAction } from "../ui";
import { AdminPricing } from "./AdminPricing";

type Subscriptions = Schema<"Subscriptions">;
type Row = Subscriptions["rows"][number];
type Settings = { trial_days: number; free_delay_hours: number; starter_areas: string[];
                  price_was: Record<string, number> };

/** Each plan's name, colour and line, in the order the bar draws them (`admin_routes.PLAN_KINDS`). */
const KINDS: Record<string, [name: string, colour: string, line: string]> = {
  paying: ["Paying", "var(--score)", "Their membership's payments go through"],
  trial: ["In a trial", "var(--accent)", "Their free days, a card kept by Stripe"],
  payment_failed: ["Payment failed", "var(--danger)", "Back on the free plan until they pay"],
  paused: ["Paused", "#d08a34", "Held at Stripe"],
  free_access: ["Free access", "#8ab4ff", "Everything, given by an admin"],
  admin: ["Admins", "var(--muted)", "Everything, as operators"],
  choosing: ["Choosing a plan", "var(--faint)", "On the page after their email link"],
  free: ["Free plan", "var(--dim)", "Jobs a week behind, no CVs or alerts"],
};

export function AdminSubscriptions() {
  const { data, failed } = useKept<Subscriptions>("/admin/subscriptions");
  if (failed && !data) return <p className="error">Could not read the subscriptions.</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const { plans, accounts } = data;
  const order = Object.keys(KINDS).filter((kind) => plans[kind]);

  return (
    <div className="subs">
      <h2>Subscriptions</h2>
      <dl className="account-tiles">
        <div>
          <dt>Members</dt>
          <dd><strong>{data.members.toLocaleString()}</strong>of {accounts.toLocaleString()} accounts</dd>
        </div>
        <div>
          <dt>Paying</dt>
          <dd>
            <strong>{(plans.paying ?? 0).toLocaleString()}</strong>
            {data.monthly !== null && data.plan ? `${money(data.monthly, data.plan.currency)} a month` : "Stripe not set up"}
          </dd>
        </div>
        <div>
          <dt>In a trial</dt>
          <dd><strong>{(plans.trial ?? 0).toLocaleString()}</strong>{data.trials_ending} ending this week</dd>
        </div>
        <div>
          <dt>Joined in 30 days</dt>
          <dd>
            <strong>{data.joined_30d.toLocaleString()}</strong>
            {data.joined_30d_members} of them members{data.cancelling ? ` · ${data.cancelling} cancelling` : ""}
          </dd>
        </div>
      </dl>

      <section className="card">
        <h2>Who is on which plan</h2>
        {/* Every account once, a part of the bar each, so the plans read at a glance. */}
        <div className="plan-bar" role="img"
             aria-label={order.map((kind) => `${KINDS[kind]![0]}: ${plans[kind]}`).join(", ")}>
          {order.map((kind) => (
            <i key={kind} style={{ flexGrow: plans[kind], background: KINDS[kind]![1] }} />
          ))}
        </div>
        <ul className="plan-kinds">
          {Object.entries(KINDS).map(([kind, [name, colour, line]]) => (
            <li key={kind} className={plans[kind] ? undefined : "none"}>
              <i style={{ background: colour }} aria-hidden="true" />
              <strong>{(plans[kind] ?? 0).toLocaleString()}</strong>
              <span>{name}<small>{line}</small></span>
            </li>
          ))}
        </ul>
      </section>

      <ThePlan subscriptions={data} />
      <AdminPricing />

      <section className="card">
        <h2>Every subscription</h2>
        <p className="hint">Payment failed first, then trials by when they end. As stored, from Stripe's last word.</p>
        {data.rows.length ? (
          <div className="table-scroll">
            <table className="subs-table">
              <thead>
                <tr>
                  <th scope="col">Account</th>
                  <th scope="col">Plan</th>
                  <th scope="col">Stripe says</th>
                  <th scope="col">Next</th>
                  <th scope="col">Last payment</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => <SubscriptionLine key={row.id} row={row} />)}
              </tbody>
            </table>
          </div>
        ) : <p className="muted">No subscriptions yet.</p>}
      </section>
    </div>
  );
}

function SubscriptionLine({ row }: { row: Row }) {
  const [name, colour] = KINDS[row.plan] ?? [row.plan, "var(--dim)"];
  const next = row.cancels_at ? `Ends ${day(row.cancels_at)}`
    : row.plan === "trial" && row.trial_ends_at ? `Trial ends ${day(row.trial_ends_at)}`
      : row.period_ends_at && row.plan === "paying" ? `Pays ${day(row.period_ends_at)}`
        : row.free_access_at ? `Free access since ${day(row.free_access_at)}` : "—";
  return (
    <tr>
      <td><a href={`#/admin/users/${row.id}`}>{row.email}</a></td>
      <td><span className="plan-dot" style={{ background: colour }} aria-hidden="true" />{name}</td>
      <td><code>{row.subscription_status ?? "none"}</code></td>
      <td>{next}</td>
      <td>
        {row.last_paid_at && row.last_paid_amount !== null && row.currency
          ? `${money(row.last_paid_amount, row.currency)}, ${day(row.last_paid_at)}` : "—"}
      </td>
    </tr>
  );
}

/** The plans: each one's price as Stripe has it and the price it is shown as reduced from, the settings that
 * come with them, and what the Starter plan includes, a switch each (owner, 2026-11-16). */
function ThePlan({ subscriptions }: { subscriptions: Subscriptions }) {
  const kept = useKept<{ settings: Settings }>("/admin/settings");
  // What the cards will say: read again after each change, so the preview below follows it.
  const pricing = useKept<Pricing>("/pricing");
  const [saved, setSaved] = useState<string | null>(null);
  const save = useAction(async (key: keyof Settings, value: Settings[keyof Settings]) => {
    const body = await api.put<{ settings: Settings }>(`/admin/settings/${key}`, { value });
    kept.set(() => body);
    setSaved(key);
    await pricing.reload();
  });
  const settings = kept.data?.settings;
  const plan = subscriptions.plan;
  const starter = pricing.data?.starter ?? null;
  // "29.99" typed is 2999 kept, Stripe's unit; nothing typed shows no reduced-from price.
  const was = (kind: "member" | "starter", of: Pricing["plan"]) => settings ? (
    <label>
      <span className="label">{kind === "member" ? "Member" : "Starter"} is reduced from</span>
      <input type="number" min={0} step={0.01} inputMode="decimal" disabled={save.busy}
             aria-label={`${kind === "member" ? "Member" : "Starter"} is reduced from`}
             defaultValue={settings.price_was[kind] ? (settings.price_was[kind] / 100).toFixed(2) : ""}
             onBlur={(event) => {
               const amount = Math.round(Number(event.target.value || 0) * 100);
               if (amount !== (settings.price_was[kind] ?? 0)) {
                 void save.run("price_was", { ...settings.price_was, [kind]: amount });
               }
             }} />
      <span className="hint">
        {saved === "price_was" ? "Saved. " : ""}Crossed out on its card beside{" "}
        {of ? `${price(of)}, with ${saving(of.amount, settings.price_was[kind]) ?? 0}% off` : "Stripe's price"}.
        Empty shows none. It must be a price people do pay, or will: a made-up one is against the law.
      </span>
    </label>
  ) : null;
  return (
    <section className="card">
      <h2>The plans</h2>
      <div className="plan-facts">
        <div>
          <span className="label">Member</span>
          <strong>{plan ? price(plan) : "Not set up"}</strong>
          <span className="hint">Set at Stripe: a new price is a new STRIPE_PRICE_ID.</span>
        </div>
        <div>
          <span className="label">Starter</span>
          <strong>{starter ? price(starter) : "Not on offer"}</strong>
          <span className="hint">
            {starter ? "Set at Stripe: STRIPE_STARTER_PRICE_ID." : "Give it a price at Stripe and set "
              + "STRIPE_STARTER_PRICE_ID: its card and its checkout appear by themselves."}
          </span>
        </div>
        {was("member", plan)}
        {was("starter", starter)}
        {settings ? (
          <>
            <label>
              <span className="label">Free trial</span>
              <select value={settings.trial_days} disabled={save.busy} aria-label="Free trial"
                      onChange={(event) => void save.run("trial_days", Number(event.target.value))}>
                {[0, 3, 7, 14, 30].map((n) => <option key={n} value={n}>{n ? `${n} days` : "None"}</option>)}
              </select>
              <span className="hint">
                {saved === "trial_days" ? "Saved. " : ""}Free days before Stripe takes the first payment, for somebody
                who never had a membership.
              </span>
            </label>
            <label>
              <span className="label">Free accounts see a job</span>
              <select value={settings.free_delay_hours} disabled={save.busy} aria-label="Free accounts see a job"
                      onChange={(event) => void save.run("free_delay_hours", Number(event.target.value))}>
                {[...new Set([0, 24, 72, 168, 336, settings.free_delay_hours])].sort((a, b) => a - b).map((n) => (
                  <option key={n} value={n}>{n ? `${n < 48 ? `${n} hours` : `${n / 24} days`} later` : "At once"}</option>
                ))}
              </select>
              <span className="hint">
                {saved === "free_delay_hours" ? "Saved. " : ""}How long after a member an account on the free plan sees
                each job. CVs, cover letters and alerts stay members'.
              </span>
            </label>
          </>
        ) : null}
      </div>
      {settings && pricing.data ? (
        <fieldset className="starter-areas">
          <legend className="label">Starter includes</legend>
          {pricing.data.areas.map((area) => (
            <label key={area.key}>
              <input type="checkbox" checked={settings.starter_areas.includes(area.key)} disabled={save.busy}
                     onChange={(event) => void save.run("starter_areas", event.target.checked
                       ? [...settings.starter_areas, area.key]
                       : settings.starter_areas.filter((each) => each !== area.key))} />
              {area.words}
            </label>
          ))}
          <span className="hint">
            {saved === "starter_areas" ? "Saved. " : ""}What the Starter plan has of a membership. A switch works
            at once for everyone on Starter, and its card says the same. Member has all of them.
          </span>
        </fieldset>
      ) : null}
      <Error>{save.error}</Error>
    </section>
  );
}
