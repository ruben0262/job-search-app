/** Becoming a member later, from the free plan (owner, 2026-11-12): what it adds, and Stripe's own checkout in the
 * page. Where the jobs page's banner, a Make button and Settings send a free account. Somebody whose payment
 * failed is sent on to Stripe's portal to pay it instead, since their membership is still there. A page of its
 * own, laid out as the plan page's checkout is, with the way back at its top. */
import { useEffect, useState, type CSSProperties } from "react";

import { api, type Membership } from "../api";
import { useKept } from "../kept";
import type { Pricing } from "../landing/Landing";
import { MEMBERSHIP, StripeCheckout, openPortal, price, standing } from "../membership";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { Brand } from "../wheel";

/** Free to member, shown over and over (owner, 2026-11-13: "switch back to free to show differences constantly"):
 * Free with nothing ticked, then Member with each thing it adds ticked in turn, a while to read them, and back.
 * It rests on Member once they touch the page or step into Stripe's form ("if the user interrupts it stops"),
 * or from the start when they ask for less motion. A tap on either side of the switch shows that side. */
const TICK_FIRST_MS = 600;
const TICK_MS = 500;
const FREE_MS = 2200;
const READ_MS = 3500;

function useSwitching(count: number): ["free" | "member", (side: "free" | "member") => void] {
  const [still] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [on, setOn] = useState<"free" | "member">(still ? "member" : "free");
  const [touched, setTouched] = useState(still);
  useEffect(() => {
    if (touched) return;
    const timer = window.setTimeout(() => setOn(on === "free" ? "member" : "free"),
      on === "free" ? FREE_MS : TICK_FIRST_MS + count * TICK_MS + READ_MS);
    return () => window.clearTimeout(timer);
  }, [on, touched, count]);
  useEffect(() => {
    if (touched) return;
    const stop = () => {
      setTouched(true);
      setOn("member");
    };
    // A tap in Stripe's form is not this page's: it shows as the page losing focus to Stripe's frame.
    const left = () => {
      if (document.activeElement instanceof HTMLIFrameElement) stop();
    };
    window.addEventListener("pointerdown", stop);
    window.addEventListener("keydown", stop);
    window.addEventListener("blur", left);
    return () => {
      window.removeEventListener("pointerdown", stop);
      window.removeEventListener("keydown", stop);
      window.removeEventListener("blur", left);
    };
  }, [touched]);
  return [on, (side) => {
    setTouched(true);
    setOn(side);
  }];
}

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
  const [on, show] = useSwitching(pricing.data?.cards.member.benefits.length ?? 0);

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
              <div className={`upgrade-switch ${on}`} aria-hidden="true">
                <i /><span onClick={() => show("free")}>Free</span><span onClick={() => show("member")}>Member</span>
              </div>
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
                <ul className={`adds ${on}`}>
                  {pricing.data.cards.member.benefits.map((line, i) => (
                    <li key={line} style={{ "--delay": `${TICK_FIRST_MS + i * TICK_MS}ms` } as CSSProperties}>
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
