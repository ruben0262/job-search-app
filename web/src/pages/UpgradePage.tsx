/** Becoming a member later, from the free plan (owner, 2026-11-12): what it adds, and Stripe's own checkout in the
 * page; or Starter, once it is sold (owner, 2026-11-16: "we are also selling starter"). Where the jobs page's
 * banner, a Make button and Settings send a free account. Somebody whose payment failed is sent on to Stripe's
 * portal to pay it instead, since their membership is still there. A page of its own, laid out as the plan
 * page's checkout is, with the way back at its top. */
import { useEffect, useState, type CSSProperties } from "react";

import { api, type Membership } from "../api";
import { useKept } from "../kept";
import type { Pricing } from "../landing/pricecards";
import { MEMBERSHIP, PLAN_NAMES, StripeCheckout, openPortal, price, standing } from "../membership";
import { ThemeToggle } from "../theme";
import { Error, Explain, useAction } from "../ui";
import { Brand } from "../wheel";
import { go } from "../route";

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

type Line = Pricing["lines"]["free"]["gives"][number];
type Paid = "starter" | "member";

/** What the membership gives that Starter does not: never Starter's crossed-out lines, which are a few picked
 * in Admin (owner, 2026-11-17: "3-4 negatives"), not all it adds. */
function beyondStarter(lines: Pricing["lines"]): Line[] {
  const had = new Set(lines.starter.adds.map((line) => line.text));
  return lines.member.adds.filter((line) => !had.has(line.text));
}

/** Back to the page they came from, or to their jobs when this page was opened on its own. */
function back(): void {
  if (history.length > 1) history.back();
  else go("/");
}

export function UpgradePage({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const { data: m, reload } = useKept<Membership>(MEMBERSHIP);
  // What each plan adds to Free, worked out by the server from the plans the site keeps (`adds`).
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
  // Which plan they are buying: the membership, the offer, or Starter once it has a price to pay.
  const [chosen, choose] = useState<Paid>("member");
  const buying: Paid = m?.starter ? chosen : "member";
  const cost = buying === "starter" ? m?.starter : m?.plan;
  // Free days on the plan being bought, for somebody who never had a subscription: the membership's, none on
  // Starter (owner, 2026-11-17).
  const days = cost && m?.trial_days ? cost.trial_days : 0;
  const adds = pricing.data?.lines[buying].adds ?? [];
  const [on, show] = useSwitching(adds.length);

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="plan-page">
        <header className="shell-top">
          <Brand href="/" />
          <ThemeToggle />
        </header>
        {m?.member && m.tier !== "starter" ? null : (
          <button type="button" className="back" onClick={back}>← Back</button>
        )}
        <section className="page-in">
          {!m ? (
            <p className="muted">Loading…</p>
          ) : m.tier === "starter" && !m.free_access ? (
            // Starter is changed for the membership where every subscription is changed: Stripe's portal.
            <div className="upgrade-done">
              <h1>Move up to Member</h1>
              <p className="lede">You're on Starter. Change your plan at Stripe, and all of this opens at once.</p>
              {pricing.data ? <Adds lines={beyondStarter(pricing.data.lines)} on="member" /> : null}
              <Error>{portal.error}</Error>
              <button type="button" className="main" disabled={portal.busy} onClick={() => void portal.run()}>
                {portal.busy ? "Opening Stripe…" : "Change my plan"}
              </button>
            </div>
          ) : m.member ? (
            <div className="upgrade-done">
              <h1>You're a member</h1>
              <p className="lede">{standing(m)}</p>
              <a className="button main" href="/">Continue</a>
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
              <h1>{buying === "starter" ? "Start with Starter" : "Become a member"}</h1>
              {m.starter ? (
                <div className="toggles plan-pick" role="group" aria-label="Plan">
                  {(["starter", "member"] as const).map((each) => (
                    <button key={each} type="button" className="toggle" aria-pressed={buying === each}
                            onClick={() => choose(each)}>{PLAN_NAMES[each]}</button>
                  ))}
                </div>
              ) : null}
              <div className={`upgrade-switch ${on}`} aria-hidden="true">
                <i /><span onClick={() => show("free")}>Free</span>
                <span onClick={() => show("member")}>{PLAN_NAMES[buying]}</span>
              </div>
              {cost ? (
                <p className="plan-price">
                  {price(cost)}{days ? <span>, after {days} days free</span> : null}
                </p>
              ) : null}
              <p className="lede">
                {days ? "Stripe keeps your card, and nothing is taken until the free days end."
                  : "Paid securely through Stripe. Cancel any time."}
              </p>
              {pricing.data ? <Adds lines={adds} on={on} /> : null}
              {key ? <StripeCheckout publishableKey={key} plan={buying} onPaid={() => void paid()} />
                : <p className="error">Payments aren't set up yet. Try again later.</p>}
            </>
          )}
        </section>
      </main>
    </>
  );
}

/** What moving up adds, a tick each: ticked in turn while `on` is "member", as the switch above shows it. */
function Adds({ lines, on }: { lines: Line[]; on: "free" | "member" }) {
  return (
    <ul className={`adds ${on}`}>
      {lines.map((line, i) => (
        <li key={line.text} style={{ "--delay": `${TICK_FIRST_MS + i * TICK_MS}ms` } as CSSProperties}>
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="10" cy="10" r="10" /><path d="M6 10.4l2.6 2.6 5.6-5.6" pathLength={1} />
          </svg>
          <span>{line.text}<Explain text={line.more} about={line.text} /></span>
        </li>
      ))}
    </ul>
  );
}
