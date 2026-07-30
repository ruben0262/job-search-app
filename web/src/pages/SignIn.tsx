/**
 * Sign in and sign up, on one screen — the first thing behind the landing page's CTAs, so it opens
 * with the landing's vocabulary: wordmark, kicker, one big title, and no box drawn around a form
 * that is already the only thing on the page (the onboarding rule).
 *
 * **Signup never says whether an address is already registered.** The server answers identically
 * either way, and this form has to match — a message here saying "that email is taken" would hand
 * back the account-existence oracle the API deliberately does not have.
 */
import { useState } from "react";

import { api } from "../api";
import { Error, Field, useAction } from "../ui";
import { ThemeToggle } from "../theme";

export function SignIn({
  onSignedIn,
  initialMode = "in",
}: {
  onSignedIn: () => void;
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
    onSignedIn();
  });

  if (sent) {
    return (
      <AuthShell kicker="One more step" title="Check your email">
        <p className="muted">
          If <strong>{email}</strong> can be registered, a confirmation link is on its way. Open it
          on this device and you land back here, signed up and ready.
        </p>
        <p className="facts">
          Nothing arriving? Check spam, or sign up again for a fresh link — the newest one is the
          one that counts.
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      kicker={mode === "in" ? "Welcome back" : "Get started"}
      title={mode === "in" ? "Sign in" : "Create your account"}
    >
      <p className="muted" style={{ marginBottom: "1.75rem" }}>
        Jobs found for you, scored by a model trained on your own judgement.{" "}
        <a href="#/privacy">What we do with your data</a>.
      </p>

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
          hint={mode === "up" ? "At least 12 characters. Longer beats complicated." : undefined}
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
      </p>
    </AuthShell>
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
