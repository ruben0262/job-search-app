/** The membership (Stripe, owner 2026-11-11): Stripe's payment fields drawn inside the site's own page, in its
 * colours and either theme (owner, 2026-11-12), Stripe's portal behind a button, and where a membership stands
 * in words. Stripe decides all of it (`billing_routes.py`); this says it. */
import { useEffect, useRef, useState } from "react";
import type { Appearance, StripeCheckoutConfirmResult, StripeCheckoutElementsSdk,
  StripeCheckoutSession } from "@stripe/stripe-js";

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

/** The site's own colours, read off the page as they are now, for Stripe's fields: its purple, its raised
 * panels, its ink, the hairline and ring its own inputs have. Not its font, which a frame from Stripe cannot
 * load from this site, so the system's sans in its place. */
function appearance(): Appearance {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string) => css.getPropertyValue(name).trim();
  return {
    theme: document.documentElement.dataset.theme === "light" ? "stripe" : "night",
    variables: {
      colorPrimary: token("--accent"), colorBackground: token("--bg-raised"), colorText: token("--ink"),
      colorTextSecondary: token("--muted"), colorTextPlaceholder: token("--dim"), colorDanger: token("--danger"),
      fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', fontSizeBase: "15px",
      borderRadius: token("--radius"),
    },
    rules: {
      ".Input, .Tab, .PickerItem": { border: `1px solid ${token("--faint")}`, boxShadow: "none" },
      ".Input:focus, .Tab:focus, .Tab--selected": {
        borderColor: token("--accent"), boxShadow: `0 0 0 4px rgba(${token("--accent-rgb")}, 0.2)` },
      ".Label": { fontWeight: "600" },
    },
  };
}

/** What a trial ends on, from Stripe's seconds. */
const until = (seconds: number) => day(new Date(seconds * 1000).toISOString());

/** Stripe's payment fields in this page, with the site's own button: the card goes to Stripe and never reaches
 * this site. Stripe's script is fetched when the form is first shown, so no other page asks Stripe for
 * anything. The price is the session's, in the currency they pick (Stripe's Adaptive Pricing). */
export function PaymentForm({ publishableKey, onPaid }: { publishableKey: string; onPaid: () => void }) {
  const fields = useRef<HTMLDivElement>(null);
  const currency = useRef<HTMLDivElement>(null);
  const [session, setSession] = useState<StripeCheckoutSession | null>(null);
  const [confirm, setConfirm] = useState<(() => Promise<StripeCheckoutConfirmResult>) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paying, setPaying] = useState(false);

  useEffect(() => {
    let gone = false;
    let checkout: StripeCheckoutElementsSdk | null = null;
    // The theme button changes the page's colours, and so Stripe's.
    const theme = new MutationObserver(() => checkout?.changeAppearance(appearance()));
    void (async () => {
      const { loadStripe } = await import("@stripe/stripe-js/pure");
      const stripe = await loadStripe(publishableKey);
      if (!stripe) throw new globalThis.Error("Stripe's script did not load");
      // Gone already, as React's second look in development leaves the first: no session is opened for it.
      if (gone || !fields.current || !currency.current) return;
      checkout = stripe.initCheckoutElementsSdk({
        clientSecret: api.post<{ client_secret: string }>("/billing/checkout").then((body) => body.client_secret),
        elementsOptions: { appearance: appearance() },
        adaptivePricing: { allowed: true },
      });
      checkout.on("change", (next) => {
        if (!gone) setSession(next);
      });
      // Stripe asks for its currency picker wherever it offers a local price.
      checkout.createCurrencySelectorElement().mount(currency.current);
      // Without Link's "save my information", which asks for a phone number and, opening as the card is
      // finished, moved the button out from under a click (2026-11-12).
      checkout.createPaymentElement({ wallets: { link: "never" } }).mount(fields.current);
      theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      const loaded = await checkout.loadActions();
      if (gone) return;
      if (loaded.type === "error") throw new globalThis.Error(loaded.error.message);
      setSession(loaded.actions.getSession());
      setConfirm(() => () => loaded.actions.confirm({ redirect: "if_required" }));
    })().catch((caught: unknown) => {
      if (!gone) {
        setError(caught instanceof ApiError ? caught.detail
          : "The payment form didn't load. Check your connection and reload the page.");
      }
    });
    return () => {
      gone = true;
      theme.disconnect();
      checkout?.getPaymentElement()?.destroy();
      checkout?.getCurrencySelectorElement()?.destroy();
    };
  }, [publishableKey]);

  const pay = async () => {
    if (!confirm) return;
    setPaying(true);
    setError(null);
    const result = await confirm().catch(() => null);
    if (result?.type === "success") {
      onPaid();
      return;
    }
    setError(result?.error.message ?? "That didn't go through. Try again.");
    setPaying(false);
  };

  const recurring = session?.recurring;
  const trial = recurring?.trial;
  const then = recurring ? `${recurring.dueNext.total.amount} ${every(recurring.interval, recurring.intervalCount)}` : "";
  return (
    <form className="pay-form" onSubmit={(event) => {
      event.preventDefault();
      void pay();
    }}>
      {recurring ? (
        <p className="pay-price">
          {trial ? <>Free until {until(trial.trialEnd)}, then <strong>{then}</strong>. Nothing to pay today.</>
            : <><strong>{then}</strong>, from today.</>}
        </p>
      ) : error ? null : <p className="muted">Loading the payment form…</p>}
      <div ref={currency} />
      <div ref={fields} />
      <Error>{error}</Error>
      {session ? (
        <>
          <button type="submit" className="main" disabled={!confirm || !session.canConfirm || paying}>
            {paying ? "Starting…" : trial ? "Start my free trial" : `Pay ${session.total.total.amount}`}
          </button>
          <p className="small muted">Cancel any time in Settings{trial ? ", and before it ends you pay nothing" : ""}.</p>
        </>
      ) : null}
    </form>
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
