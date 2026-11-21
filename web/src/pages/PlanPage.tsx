/** Free, Starter or the membership, picked between the email link and setting up (owner, 2026-11-12: "after
 * sign up and email confirmation and before the actual onboarding"), on the landing page's own cards. Free goes
 * on to setting up with no card asked. A paid plan shows Stripe's own checkout here, and nothing goes on until
 * Stripe says it is done (`libs.membership.choosing`); the way back is to the cards, not past them. */
import { useState } from "react";

import { api, type Membership } from "../api";
import { useKept } from "../kept";
import { PriceCards, type Pricing } from "../landing/pricecards";
import { MEMBERSHIP, StripeCheckout, price } from "../membership";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { Brand } from "../wheel";
import { planAsked } from "./SignIn";
import { go } from "../route";

export function PlanPage({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const pricing = useKept<Pricing>("/pricing");
  const membership = useKept<Membership>(MEMBERSHIP);
  const key = membership.data?.publishable_key ?? null;
  // A paid card on the landing page carries its plan through the email link: its checkout, at once.
  const [paying, setPaying] = useState<"member" | "starter" | null>(() => {
    const asked = planAsked();
    return asked === "free" ? null : asked;
  });
  // The form made as the page opens is the membership's, the offer; Starter's is made when it is picked.
  const [form, setForm] = useState<"member" | "starter">(paying ?? "member");
  const pay = (plan: "member" | "starter") => {
    setForm(plan);
    setPaying(plan);
  };
  const free = useAction(async () => {
    await api.post("/billing/free");
    await onChanged();
    go("/");
  });
  const paid = async () => {
    await api.post("/billing/refresh").catch(() => undefined);
    await onChanged();
    go("/");
  };
  const plan = (form === "starter" ? pricing.data?.starter : pricing.data?.plan) ?? null;
  // The plan's own free days: the membership's, and none on Starter (owner, 2026-11-17).
  const days = plan?.trial_days ?? 0;

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="plan-page">
        <header className="shell-top">
          <Brand href="/" />
          <ThemeToggle />
        </header>
        {/* Stripe's checkout is made as the page opens and drawn, unseen and out of reach, under the cards while
            they are read, so the membership's button shows it at once: made on the tap, it took 4.2-4.9 s, three
            of them Stripe's own (owner, 2026-11-12: "ready before the tap"). Hidden outright, the browser stops
            drawing Stripe's frame, and the tap still waited 1-2 s. */}
        <section className={paying ? "page-in" : "plan-waiting"} inert={!paying}>
          <button type="button" className="back" onClick={() => setPaying(null)}>← Back to the plans</button>
          <h1>{form === "starter" ? "Start with Starter" : "Start your membership"}</h1>
          {/* What it costs, large, over Stripe's page, which says it smaller and in their own currency. */}
          {plan ? <p className="plan-price">{price(plan)}{days ? <span>, after {days} days free</span> : null}</p> : null}
          <p className="lede">
            {days ? "Stripe keeps your card, and nothing is taken until the free days end."
              : "Paid securely through Stripe. Cancel any time."}
          </p>
          {key ? <StripeCheckout publishableKey={key} plan={form} onPaid={() => void paid()} />
            : membership.data ? <p className="error">Payments aren't set up yet. Pick Free for now.</p> : null}
        </section>
        <section className="page-in" hidden={paying !== null}>
          <h1>Choose your plan</h1>
          <p className="lede">Start free, or get every job first. You can change it any time.</p>
          {pricing.data ? (
            <div className="landing">
              <PriceCards pricing={pricing.data} busy={free.busy}
                          onChoose={(kind) => (kind === "free" ? void free.run() : pay(kind))} />
            </div>
          ) : pricing.failed ? <p className="error">The plans didn't load. Reload the page.</p>
            : <p className="muted">Loading…</p>}
          <Error>{free.error}</Error>
        </section>
      </main>
    </>
  );
}
