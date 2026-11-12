/** Becoming a member later, from the free plan (owner, 2026-11-12): what it adds, and Stripe's own checkout in the
 * page. Where the jobs page's banner, a Make button and Settings send a free account. Somebody whose payment
 * failed is sent on to Stripe's portal to pay it instead, since their membership is still there. A page of its
 * own, laid out as the plan page's checkout is, with the way back at its top. */
import type { CSSProperties } from "react";

import { api, type Membership } from "../api";
import { useKept } from "../kept";
import type { Pricing } from "../landing/Landing";
import { MEMBERSHIP, StripeCheckout, openPortal, price, standing } from "../membership";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { Brand } from "../wheel";

/** Back to the page they came from, or to their jobs when this page was opened on its own. */
function back(): void {
  if (history.length > 1) history.back();
  else window.location.hash = "/";
}

export function UpgradePage({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const { data: m, reload } = useKept<Membership>(MEMBERSHIP);
  // What a membership adds: the landing page's Member card, as Admin words it, not a second copy here.
  const pricing = useKept<Pricing>("/pricing");
  const key = m?.publishable_key ?? null;
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
      <main className="plan-page">
        <header className="shell-top">
          <Brand href="#/" />
          <ThemeToggle />
        </header>
        {m?.member ? null : <button type="button" className="back" onClick={back}>← Back</button>}
        <section className="page-in">
          {!m ? (
            <p className="muted">Loading…</p>
          ) : m.member ? (
            <div className="upgrade-done">
              <h1>You're a member</h1>
              <p className="lede">{standing(m)}</p>
              <a className="button main" href="#/">Continue</a>
            </div>
          ) : !m.can_start ? (
            <div className="upgrade-done">
              <h1>{owed ? "Your last payment didn't go through" : "Your membership is paused"}</h1>
              <p className="lede">{owed ? "Pay it at Stripe" : "Resume it at Stripe"}, and everything opens again.</p>
              <Error>{portal.error}</Error>
              <button type="button" className="main" disabled={portal.busy} onClick={() => void portal.run()}>
                {portal.busy ? "Opening Stripe…" : owed ? "Pay now" : "Resume"}
              </button>
            </div>
          ) : (
            <>
              <h1>Become a member</h1>
              {/* Free to member, shown: the switch slides over, then each thing it adds is ticked in turn
                  (owner, 2026-11-12: "see the benefits being ticked"). */}
              <div className="upgrade-switch" aria-hidden="true"><i /><span>Free</span><span>Member</span></div>
              {m.plan ? (
                <p className="plan-price">
                  {price(m.plan)}{m.trial_days ? <span>, after {m.trial_days} days free</span> : null}
                </p>
              ) : null}
              <p className="lede">
                {m.trial_days ? "Stripe keeps your card, and nothing is taken until the free days end."
                  : "Paid securely through Stripe. Cancel any time."}
              </p>
              {pricing.data ? (
                <ul className="adds">
                  {pricing.data.cards.member.benefits.map((line, i) => (
                    <li key={line} style={{ "--i": i } as CSSProperties}>
                      <svg viewBox="0 0 20 20" aria-hidden="true">
                        <circle cx="10" cy="10" r="10" /><path d="M6 10.4l2.6 2.6 5.6-5.6" pathLength={1} />
                      </svg>
                      {line}
                    </li>
                  ))}
                </ul>
              ) : null}
              {key ? <StripeCheckout publishableKey={key} onPaid={() => void paid()} />
                : <p className="error">Payments aren't set up yet. Try again later.</p>}
            </>
          )}
        </section>
      </main>
    </>
  );
}
