/** Sign in and sign up, on one screen — the first thing behind the landing page's CTAs. Signing up picks the plan
 * (owner, 2026-11-12): free, which asks for no card, or the membership, whose payment form follows the email
 * link; and agrees to the privacy policy and its cookies, the only way to an account. */
import { useEffect, useState } from "react";

import { ApiError, api, type Plan, type Schema } from "../api";
import { price } from "../membership";
import { span } from "../time";
import { Error, Field, useAction, useNewPassword } from "../ui";
import { ThemeToggle } from "../theme";
import { Brand, Wheel } from "../wheel";

type Picked = "free" | "member";

/** The plan the address asks for: the landing page's Member card is `#/signup?plan=member`; anything else is
 * free. Read again by the email link's page (`Verify`), which carries it on. */
export function planAsked(): Picked {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("plan") === "member" ? "member" : "free";
}

export function SignIn({
  onSignedIn,
  mode,
}: {
  /** Resolves once the dashboard has been read, which is what makes the route safe to change. */
  onSignedIn: () => void | Promise<void>;
  /** From the address (`#/signin`, `#/signup`), and from nothing else. */
  mode: "in" | "up";
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const chosen = useNewPassword();
  const [picked, setPicked] = useState<Picked>(planAsked);
  const [agreed, setAgreed] = useState(false);
  const [sent, setSent] = useState(false);
  // What the box agrees to (`/legal/privacy`), and what a membership costs (`/billing/plan`): none while Stripe
  // is not set up, and then the free plan is the only one.
  const [policy, setPolicy] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  useEffect(() => {
    if (mode !== "up") return;
    void api.get<Schema<"Disclosure">>("/legal/privacy").then((body) => setPolicy(body.version)).catch(() => undefined);
    void api.get<Plan | null>("/billing/plan").then(setPlan).catch(() => undefined);
  }, [mode]);
  const member = plan !== null && picked === "member";

  const submit = useAction(async () => {
    if (mode === "up") {
      if (!agreed) throw new ApiError(0, "Tick the box to agree to the privacy policy and cookies.");
      if (!policy) throw new ApiError(0, "The privacy policy didn't load. Reload the page and try again.");
      await api.post("/auth/signup", { email, password: chosen.check(), consent_version: policy,
                                       plan: member ? "member" : "free" });
      setSent(true);
      return;
    }
    await api.post("/auth/login", { email, password });
    // The session first, then the route. Changing the hash while the app still believes it is
    // signed out routes `/` to the landing page for as long as the dashboard takes to answer —
    // which is the flash of marketing copy people saw between pressing Sign in and arriving.
    await onSignedIn();
    // Land on the overview, not on `#/signin`.
    window.location.hash = "/";
  });

  if (sent) {
    return (
      <AuthShell title="Check your email">
        <p className="muted">
          If <strong>{email}</strong> can be registered, a confirmation link is on its way. Open it
          on this device and you land back here, signed up{member ? ", at the form that starts your free trial"
            : " and ready"}.
        </p>
        <p className="facts">Nothing arriving? Check spam, or have a fresh link sent.</p>
        <ResendLink email={email} plan={member ? "member" : "free"} />
      </AuthShell>
    );
  }

  return (
    <AuthShell title={mode === "in" ? "Sign in" : "Create your account"}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit.run();
        }}
      >
        {mode === "up" && plan ? <Plans plan={plan} picked={picked} onPick={setPicked} /> : null}
        <Field label="Email">
          <input
            type="email"
            value={email}
            autoComplete="email"
            required
            autoFocus
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        {mode === "up" ? chosen.fields : (
          <Field label="Password">
            <input
              type="password"
              value={password}
              autoComplete="current-password"
              required
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
        )}
        {mode === "up" ? (
          <label className="agree">
            <input type="checkbox" checked={agreed} required onChange={(event) => setAgreed(event.target.checked)} />
            <span>
              I agree to the <a href="#/privacy" target="_blank" rel="noreferrer">privacy policy and cookies</a>.
            </span>
          </label>
        ) : null}

        <Error>{submit.error}</Error>
        <div className="row" style={{ marginTop: "1.5rem" }}>
          <button type="submit" disabled={submit.busy}>
            {submit.busy ? "Working…" : mode === "in" ? "Sign in" : "Create account"}
          </button>
        </div>
      </form>

      <p className="facts" style={{ marginTop: "2rem" }}>
        {mode === "in" ? "New here? " : "Already have an account? "}
        <a
          href={mode === "in" ? "#/signup" : "#/signin"}
        >
          {mode === "in" ? "Create an account" : "Sign in instead"}
        </a>
        {mode === "in" ? (
          <>
            {" · "}
            <a href="#/forgot">Forgot your password?</a>
          </>
        ) : null}
      </p>
    </AuthShell>
  );
}

/** The two plans, the membership's price and free days as Stripe and Admin have them. */
function Plans({ plan, picked, onPick }: { plan: Plan; picked: Picked; onPick: (picked: Picked) => void }) {
  const options: [Picked, string, string][] = [
    ["free", "Free", `Every job that fits you, ${span(plan.free_delay_hours)} after members see it. No card.`],
    ["member", "Member", `${plan.trial_days ? `${plan.trial_days} days free, then ${price(plan)}` : price(plan)}. `
      + "Every job the moment it's found, CVs and cover letters, and alerts."],
  ];
  return (
    <fieldset className="plans">
      <legend className="label">Your plan</legend>
      {options.map(([key, name, line]) => (
        <label key={key} className={picked === key ? "plan on" : "plan"}>
          <input type="radio" name="plan" value={key} checked={picked === key} onChange={() => onPick(key)} />
          <span>
            <strong>{name}</strong>
            <span className="small muted">{line}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

/** A fresh confirmation email, for an account still waiting for one, going where the first one did (`plan`).
 * Asks for the address if it is not known yet. */
export function ResendLink({ email, plan }: { email: string; plan: Picked }) {
  const [address, setAddress] = useState(email);
  const [sent, setSent] = useState(false);
  const resend = useAction(async () => {
    await api.post("/auth/resend", { email: address, plan });
    setSent(true);
  });

  if (sent) return <p className="facts">Sent. The newest link is the one that works.</p>;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void resend.run();
      }}
    >
      {email ? null : (
        <Field label="Email">
          <input
            type="email"
            value={address}
            autoComplete="email"
            required
            onChange={(event) => setAddress(event.target.value)}
          />
        </Field>
      )}
      <Error>{resend.error}</Error>
      <button type="submit" className="secondary" disabled={resend.busy || !address}>
        {resend.busy ? "Sending…" : "Send me a new link"}
      </button>
    </form>
  );
}

/** The frame every signed-out page shares (sign in, sign up, the emailed links): the wordmark home, the
 * wheel lying in its halo of light, and one question under it. */
export function AuthShell({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="auth page-in">
        <header className="shell-top">
          <Brand href="#/" />
          <ThemeToggle />
        </header>
        <div className="auth-body">
          <div className="auth-art" aria-hidden="true">
            <Wheel />
          </div>
          <h1>{title}</h1>
          {children}
        </div>
      </main>
    </>
  );
}
