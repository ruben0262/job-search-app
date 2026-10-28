/** Sign in and sign up, on one screen — the first thing behind the landing page's CTAs. */
import { useState } from "react";

import { api } from "../api";
import { Error, Field, useAction } from "../ui";
import { ThemeToggle } from "../theme";

export function SignIn({
  onSignedIn,
  initialMode = "in",
}: {
  /** Resolves once the dashboard has been read, which is what makes the route safe to change. */
  onSignedIn: () => void | Promise<void>;
  initialMode?: "in" | "up";
}) {
  const [mode, setMode] = useState<"in" | "up">(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState(false);

  const submit = useAction(async () => {
    if (mode === "up") {
      await api.post("/auth/signup", { email, password });
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
      <AuthShell kicker="One more step" title="Check your email">
        <p className="muted">
          If <strong>{email}</strong> can be registered, a confirmation link is on its way. Open it
          on this device and you land back here, signed up and ready.
        </p>
        <p className="facts">Nothing arriving? Check spam, or have a fresh link sent.</p>
        <ResendLink email={email} />
      </AuthShell>
    );
  }

  return (
    <AuthShell
      kicker={mode === "in" ? "Welcome back" : "Get started"}
      title={mode === "in" ? "Sign in" : "Create your account"}
    >

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
        <Field
          label="Password"
          hint={mode === "up" ? "At least 9 characters. Longer beats complicated." : undefined}
        >
          <input
            type="password"
            value={password}
            autoComplete={mode === "in" ? "current-password" : "new-password"}
            required
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

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
          onClick={() => setMode(mode === "in" ? "up" : "in")}
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

/** A fresh confirmation email, for an account still waiting for one. Asks for the address if it is
 * not known yet. */
export function ResendLink({ email }: { email: string }) {
  const [address, setAddress] = useState(email);
  const [sent, setSent] = useState(false);
  const resend = useAction(async () => {
    await api.post("/auth/resend", { email: address });
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

/** The frame both auth states share: backdrop, wordmark, kicker, title, and a way back. */
export function AuthShell({
  kicker,
  title,
  children,
}: {
  kicker: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard page-in">
        <ThemeToggle floating />
        <nav style={{ border: "none", marginBottom: "0" }}>
          <a className="brand" href="#/">
            Mahoraga
          </a>
        </nav>
        <div className="step" style={{ paddingTop: "8vh" }}>
          <p className="kicker">{kicker}</p>
          <h1>{title}</h1>
          {children}
        </div>
      </main>
    </>
  );
}
