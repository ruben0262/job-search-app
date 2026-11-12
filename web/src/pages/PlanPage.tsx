/** Free or the membership, picked between the email link and setting up (owner, 2026-11-12: "after sign up and
 * email confirmation and before the actual onboarding"), on the landing page's own two cards. Free goes on to
 * setting up with no card asked. The membership shows Stripe's own checkout here, and nothing goes on until
 * Stripe says it is done (`libs.membership.choosing`); the way back is to the cards, not past them. */
import { useState } from "react";

import { api, type Membership } from "../api";
import { useKept } from "../kept";
import { PriceCards, type Pricing } from "../landing/Landing";
import { MEMBERSHIP, StripeCheckout, price } from "../membership";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { Brand } from "../wheel";
import { planAsked } from "./SignIn";

export function PlanPage({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const pricing = useKept<Pricing>("/pricing");
  const membership = useKept<Membership>(MEMBERSHIP);
  const key = membership.data?.publishable_key ?? null;
  // The landing page's Member card carries "member" through the email link: its checkout, at once.
  const [paying, setPaying] = useState(() => planAsked() === "member");
  const free = useAction(async () => {
    await api.post("/billing/free");
    await onChanged();
    window.location.hash = "/";
  });
  const paid = async () => {
    await api.post("/billing/refresh").catch(() => undefined);
    await onChanged();
    window.location.hash = "/";
  };
  const days = pricing.data?.trial_days ?? 0;
  const plan = pricing.data?.plan ?? null;

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="plan-page">
        <header className="shell-top">
          <Brand href="#/" />
          <ThemeToggle />
        </header>
        {/* Stripe's checkout is made as the page opens and drawn, unseen and out of reach, under the cards while
            they are read, so the membership's button shows it at once: made on the tap, it took 4.2-4.9 s, three
            of them Stripe's own (owner, 2026-11-12: "ready before the tap"). Hidden outright, the browser stops
            drawing Stripe's frame, and the tap still waited 1-2 s. */}
        <section className={paying ? "page-in" : "plan-waiting"} inert={!paying}>
          <button type="button" className="back" onClick={() => setPaying(false)}>← Back to the plans</button>
          <h1>Start your membership</h1>
          {/* What it costs, large, over Stripe's page, which says it smaller and in their own currency. */}
          {plan ? <p className="plan-price">{price(plan)}{days ? <span>, after {days} days free</span> : null}</p> : null}
          <p className="lede">
            {days ? "Stripe keeps your card, and nothing is taken until the free days end."
              : "Paid securely through Stripe."}
          </p>
          {key ? <StripeCheckout publishableKey={key} onPaid={() => void paid()} />
            : membership.data ? <p className="error">Payments aren't set up yet. Pick Free for now.</p> : null}
        </section>
        <section className="page-in" hidden={paying}>
          <h1>Choose your plan</h1>
          <p className="lede">Start free, or get every job first. You can change it any time.</p>
          {pricing.data ? (
            <div className="landing">
              <PriceCards pricing={pricing.data} busy={free.busy}
                          onChoose={(kind) => (kind === "free" ? void free.run() : setPaying(true))} />
            </div>
          ) : pricing.failed ? <p className="error">The plans didn't load. Reload the page.</p>
            : <p className="muted">Loading…</p>}
          <Error>{free.error}</Error>
        </section>
      </main>
    </>
  );
}
