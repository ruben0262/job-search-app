/** Sign in and sign up, on one screen — the first thing behind the landing page's CTAs. Signing up asks for the
 * address, the password twice and agreement to the Terms (the policies read beside them), the only way to an account;
 * the plan is picked after the email link, before setting up (owner, 2026-11-12: "not during the initial
 * signup"). */
import { useEffect, useState } from "react";

import { ApiError, api, type Schema } from "../api";
import { Error, Field, useAction, useNewPassword } from "../ui";
import type { LegalName } from "./LegalPage";
import { ThemeToggle } from "../theme";
import { Brand, Wheel } from "../wheel";

type Picked = "free" | "starter" | "member";

/** The plan the address asks for: the landing page's Member card is `#/signup?plan=member`, its Starter card
 * `?plan=starter`; anything else is free. Carried by the email link (`Verify`) to the plan page, which opens
 * on that plan's checkout. */
export function planAsked(): Picked {
  const asked = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("plan");
  return asked === "member" || asked === "starter" ? asked : "free";
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
  const [picked] = useState<Picked>(planAsked);
  const [agreed, setAgreed] = useState(false);
  const [sent, setSent] = useState(false);
  // What the box agrees to: the legal pages' version, the same on all four (`libs.legal`).
  const [policy, setPolicy] = useState<string | null>(null);
  useEffect(() => {
    if (mode !== "up") return;
    void api.get<Schema<"LegalDocument">>("/legal/terms").then((body) => setPolicy(body.version)).catch(() => undefined);
  }, [mode]);

  const submit = useAction(async () => {
    if (mode === "up") {
      if (!agreed) throw new ApiError(0, "Tick the box to agree to the Terms.");
      if (!policy) throw new ApiError(0, "Our terms didn't load. Reload the page and try again.");
      await api.post("/auth/signup", { email, password: chosen.check(), consent_version: policy, plan: picked });
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
          If <strong>{email}</strong> can be registered, a confirmation link is on its way. Open it and
          you land back here, signed up, to choose your plan.
        </p>
        <p className="facts">Nothing arriving? Check spam, or have a fresh link sent.</p>
        <ResendLink email={email} plan={picked} />
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
            {/* Named in the box, the limit on our liability: a term like it binds only when it was pointed out and
                agreed to (Hungary's Civil Code, 6:78). Each page opens in a tab of its own, so the form keeps. */}
            <span>
              I agree to the <Page name="terms">Terms</Page>, including the limits on our liability, and I have read
              the <Page name="privacy">Privacy</Page>, <Page name="cookies">Cookie</Page> and{" "}
              <Page name="data">Data Use</Page> policies.
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

/** One legal page, opened in a tab of its own so the form keeps what was typed. */
function Page({ name, children }: { name: LegalName; children: string }) {
  return <a href={`#/${name}`} target="_blank" rel="noreferrer">{children}</a>;
}
