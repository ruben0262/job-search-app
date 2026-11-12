/** The membership (Stripe, owner 2026-11-11): Stripe's own checkout inside the site's page (owner, 2026-11-12:
 * "it needs to be stripes payment page"), Stripe's portal behind a button, and where a membership stands in
 * words. Stripe decides all of it (`billing_routes.py`); this says it. */
import { useEffect, useRef, useState } from "react";
import type { Stripe, StripeEmbeddedCheckout } from "@stripe/stripe-js";

import { ApiError, api, type Membership, type Plan } from "./api";
import { useKept } from "./kept";
import { day } from "./time";
import { Card, Error, useAction } from "./ui";

/** What Settings and the membership page read, as `kept.ts` keeps it. */
export const MEMBERSHIP = "/billing";

/** Whether this page was opened by Stripe sending them back (`billing_routes.BACK`). Taken off the address,
 * so that reloading the page does not ask Stripe again. */
export function backFromStripe(): boolean {
  if (new URLSearchParams(window.location.search).get("stripe") !== "back") return false;
  window.history.replaceState(null, "", window.location.pathname + window.location.hash);
  return true;
}

/** "€9.99", from Stripe's smallest unit (cents), written the reader's way. */
export function money(amount: number, currency: string): string {
  const format = new Intl.NumberFormat(undefined, { style: "currency", currency: currency.toUpperCase() });
  return format.format(amount / 10 ** (format.resolvedOptions().maximumFractionDigits ?? 2));
}

/** "a month", "every 3 months". */
function every(interval: string, count: number): string {
  return count === 1 ? `a ${interval}` : `every ${count} ${interval}s`;
}

/** "€9.99 a month", "€25 every 3 months". */
export function price(plan: Plan): string {
  return `${money(plan.amount, plan.currency)} ${every(plan.interval, plan.interval_count)}`;
}

type Tone = "ok" | "warn" | "down" | "unknown";

/** Stripe's word for a subscription as an operator reads it, in a word and a colour (`.verdict`). */
const STANDING: Record<string, [string, Tone]> = {
  trialing: ["Trial", "ok"], active: ["Paid", "ok"], past_due: ["Payment failed", "down"],
  unpaid: ["Unpaid", "down"], paused: ["Paused", "warn"], canceled: ["Ended", "unknown"],
  incomplete: ["Payment unfinished", "warn"], incomplete_expired: ["Ended", "unknown"],
};

export function standingWord(status: string | null | undefined, free: boolean, admin: boolean): [string, Tone] {
  if (free) return ["Free access", "ok"];
  if (status && STANDING[status]) return STANDING[status];
  return admin ? ["Admin", "unknown"] : ["Free plan", "unknown"];
}

/** Where their membership stands, in a sentence they can act on. */
export function standing(m: Membership): string {
  if (m.free_access) {
    // Stripe charges nothing while it lasts (`billing_routes.hold_billing`), and starts again after it.
    return m.can_start ? "A membership, given by the team."
      : "A membership, given by the team. Your own is on hold meanwhile, and nothing is charged for it.";
  }
  switch (m.status) {
    case "trialing":
      return m.cancels_at ? `Free trial until ${day(m.cancels_at)}. It won't renew.`
        : `Free trial${m.period_ends_at ? ` until ${day(m.period_ends_at)}` : ""}.${m.plan ? ` Then ${price(m.plan)}.` : ""}`;
    case "active":
      return m.cancels_at ? `Active until ${day(m.cancels_at)}. It won't renew.`
        : `Active.${m.period_ends_at ? ` Next payment on ${day(m.period_ends_at)}.` : ""}`;
    case "past_due":
    case "unpaid":
      return "Your last payment didn't go through, so your account is on the free plan until it does.";
    case "paused":
      return "Paused, so your account is on the free plan.";
    case "canceled":
    case "incomplete_expired":
      return `${m.cancels_at ? `Ended on ${day(m.cancels_at)}` : "Ended"}. Your account is on the free plan.`;
    default:
      return m.member ? "Open to you as an admin." : "The free plan.";
  }
}

/** To Stripe's portal, in this tab: pay what is owed, change the card, cancel. Stripe sends them back. */
export async function openPortal(): Promise<void> {
  const { url } = await api.post<{ url: string }>("/billing/portal");
  window.location.assign(url);
}

/** Stripe's script, fetched once a page and kept. */
let loading: Promise<Stripe> | null = null;

function stripeFor(publishableKey: string): Promise<Stripe> {
  loading ??= import("@stripe/stripe-js/pure").then(({ loadStripe }) => loadStripe(publishableKey)).then((stripe) => {
    if (!stripe) throw new globalThis.Error("Stripe's script did not load");
    return stripe;
  });
  // A failed fetch is asked again by the next page, not kept.
  loading.catch(() => {
    loading = null;
  });
  return loading;
}

/** The checkout shown last, or being made: Stripe allows one on a page at a time, and React mounts each twice
 * in development, so each waits until the one before it is gone. */
let showing: Promise<unknown> = Promise.resolve();

/** Stripe's own checkout inside this page (`ui_mode: embedded_page`): the price large, the trial, the card,
 * Stripe's currency switch and its button, as Stripe draws them for every shop (its look is Stripe's: Dashboard
 * → Settings → Branding). The card goes to Stripe and never reaches this site. `onPaid` when Stripe says it is
 * done, and the site reads Stripe again then, so it opens on Stripe's word rather than the webhook's timing. */
export function StripeCheckout({ publishableKey, onPaid }: { publishableKey: string; onPaid: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [drawn, setDrawn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const paid = useRef(onPaid);
  paid.current = onPaid;

  useEffect(() => {
    let gone = false;
    const mine = showing.then(async (): Promise<StripeEmbeddedCheckout | null> => {
      const stripe = await stripeFor(publishableKey);
      if (gone || !box.current) return null;
      const checkout = await stripe.createEmbeddedCheckoutPage({
        fetchClientSecret: () => api.post<{ client_secret: string }>("/billing/checkout").then((body) => body.client_secret),
        onComplete: () => paid.current(),
        onAnalyticsEvent: (event) => {
          if (event.eventType === "checkoutRendered" && !gone) setDrawn(true);
        },
      });
      if (gone || !box.current) {
        checkout.destroy();
        return null;
      }
      checkout.mount(box.current);
      return checkout;
    });
    showing = mine.catch(() => null);
    mine.catch((caught: unknown) => {
      if (!gone) {
        setError(caught instanceof ApiError ? caught.detail
          : "Stripe's checkout didn't load. Check your connection and reload the page.");
      }
    });
    return () => {
      gone = true;
      // Destroyed once it is made, and the next one waits for that.
      showing = mine.then((checkout) => checkout?.destroy(), () => undefined);
    };
  }, [publishableKey]);

  return (
    <div className="stripe-checkout">
      <Error>{error}</Error>
      {/* Under Stripe's page while it loads, and covered by it once it is drawn. */}
      {drawn || error ? null : (
        <p className="stripe-loading" role="status">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10.5V8a5 5 0 0110 0v2.5M5.5 10.5h13v10h-13z" /></svg>
          Opening Stripe's secure checkout…
        </p>
      )}
      <div ref={box} className="stripe-box" />
    </div>
  );
}

/** Settings' card: where the membership stands, the last payment, and what to do next: Stripe's portal for a
 * membership, the membership page for the free plan. */
export function MembershipCard() {
  const { data } = useKept<Membership>(MEMBERSHIP);
  const manage = useAction(openPortal);
  if (!data) return null;
  return (
    <Card title="Your membership">
      <p>{standing(data)}</p>
      {data.last_paid_at && data.last_paid_amount !== null && data.currency ? (
        <p className="small muted">
          Last payment: {money(data.last_paid_amount, data.currency)} on {day(data.last_paid_at)}.
        </p>
      ) : null}
      <div className="row">
        {!data.member && data.can_start ? <a className="button" href="#/upgrade">Become a member</a> : null}
        {data.can_manage ? (
          <button type="button" className="secondary" disabled={manage.busy} onClick={() => void manage.run()}>
            {manage.busy ? "Opening Stripe…" : data.can_start ? "Your invoices" : "Manage membership"}
          </button>
        ) : null}
      </div>
      <Error>{manage.error}</Error>
    </Card>
  );
}
