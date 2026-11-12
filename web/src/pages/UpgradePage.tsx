/** Becoming a member (owner, 2026-11-12): what it adds, and Stripe's payment fields in the page. Where the sign-up
 * page's Member choice lands once the email is confirmed, and where the jobs page's banner, a Make button and
 * Settings send a free account. Somebody whose payment failed is sent on to Stripe's portal to pay it instead,
 * since their membership is still there. A page of its own, before onboarding as after it: one job, paying, and
 * "Not now" the way out. */
import { api, type Membership } from "../api";
import { useKept } from "../kept";
import { MEMBERSHIP, PaymentForm, openPortal, standing } from "../membership";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { Brand } from "../wheel";

/** What a membership adds to the free plan, in the order people asked for it. */
const ADDS = [
  "Every new job the moment it's found, so you apply among the first",
  "A CV and a cover letter made for any job in one tap, read the way an applicant tracking system reads them",
  "Alerts on Telegram, in your browser or by email",
];

export function UpgradePage({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const { data: m, reload } = useKept<Membership>(MEMBERSHIP);
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
              <ul className="adds">
                {ADDS.map((line) => <li key={line}>{line}</li>)}
              </ul>
              {m.publishable_key ? (
                <PaymentForm publishableKey={m.publishable_key} onPaid={() => void paid()} />
              ) : (
                <p className="error">Payments aren't set up yet. Try again later.</p>
              )}
            </>
          )}
          {m && !m.member ? <p className="small"><a href="#/">Not now</a></p> : null}
        </div>
      </main>
    </>
  );
}
