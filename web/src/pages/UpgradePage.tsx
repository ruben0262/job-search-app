/** Becoming a member later, from the free plan (owner, 2026-11-12): what it adds, and Stripe's own checkout in the
 * page. Where the jobs page's banner, a Make button and Settings send a free account. Somebody whose payment
 * failed is sent on to Stripe's portal to pay it instead, since their membership is still there. A page of its
 * own: one job, paying, and "Not now" the way out. */
import { useEffect } from "react";

import { api, type Membership } from "../api";
import { useKept } from "../kept";
import type { Pricing } from "../landing/Landing";
import { MEMBERSHIP, StripeCheckout, openPortal, preloadStripe, price, standing } from "../membership";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { Brand } from "../wheel";

export function UpgradePage({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const { data: m, reload } = useKept<Membership>(MEMBERSHIP);
  // What a membership adds: the landing page's Member card, as Admin words it, not a second copy here.
  const pricing = useKept<Pricing>("/pricing");
  const key = m?.publishable_key ?? null;
  useEffect(() => {
    if (key) preloadStripe(key);
  }, [key]);
  const portal = useAction(openPortal);
  // Paid in the form: Stripe read again, so everything opens now rather than when its webhook arrives.
  const paid = async () => {
    await api.post("/billing/refresh").catch(() => undefined);
    await Promise.all([reload(), onChanged()]);
  };
  // A payment owed, or paused: the membership is still there, and is settled at Stripe.
  const owed = m?.status === "past_due" || m?.status === "unpaid";

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard">
        <header className="shell-top">
          <Brand href="#/" />
          <ThemeToggle />
        </header>
        <div className="step page-in">
          {!m ? (
            <p className="muted">Loading…</p>
          ) : m.member ? (
            <>
              <h1>You're a member</h1>
              <p className="lede">{standing(m)}</p>
              <a className="button main" href="#/">Continue</a>
            </>
          ) : !m.can_start ? (
            <>
              <h1>{owed ? "Your last payment didn't go through" : "Your membership is paused"}</h1>
              <p className="lede">{owed ? "Pay it at Stripe" : "Resume it at Stripe"}, and everything opens again.</p>
              <Error>{portal.error}</Error>
              <button type="button" className="main" disabled={portal.busy} onClick={() => void portal.run()}>
                {portal.busy ? "Opening Stripe…" : owed ? "Pay now" : "Resume"}
              </button>
            </>
          ) : (
            <>
              <h1>Become a member</h1>
              {m.plan ? (
                <p className="plan-price">
                  {price(m.plan)}{m.trial_days ? <span>, after {m.trial_days} days free</span> : null}
                </p>
              ) : null}
              {pricing.data ? (
                <ul className="adds">
                  {pricing.data.cards.member.benefits.map((line) => <li key={line}>{line}</li>)}
                </ul>
              ) : null}
              {key ? <StripeCheckout publishableKey={key} onPaid={() => void paid()} />
                : <p className="error">Payments aren't set up yet. Try again later.</p>}
            </>
          )}
          {m && !m.member ? <p className="small"><a href="#/">Not now</a></p> : null}
        </div>
      </main>
    </>
  );
}
