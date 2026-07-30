/**
 * Forgot (ask for a link) and Reset (the link lands here: `#/reset?token=…`).
 *
 * **The forgot form answers the same whatever happened** — the server is opaque about which
 * addresses have accounts, and this page has to match it rather than helpfully narrowing it down.
 * A successful reset ends every session and does not sign the user in: proving control of an inbox
 * is not a login.
 */
import { useState } from "react";

import { api } from "../api";
import { Error, Field, useAction } from "../ui";
import { AuthShell } from "./SignIn";

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  const submit = useAction(async () => {
    await api.post("/auth/forgot", { email });
    setSent(true);
  });

  if (sent) {
    return (
      <AuthShell kicker="One more step" title="Check your email">
        <p className="muted">
          If <strong>{email}</strong> has an account, a reset link is on its way. It works once and
          expires in an hour — the newest one is the one that counts.
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell kicker="It happens" title="Reset your password">
      <p className="muted" style={{ marginBottom: "1.75rem" }}>
        Tell us the address you signed up with and we send a reset link.
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
        <Error>{submit.error}</Error>
        <button type="submit" disabled={submit.busy}>
          {submit.busy ? "Sending…" : "Send reset link"}
        </button>
      </form>
      <p className="facts" style={{ marginTop: "2rem" }}>
        Remembered it? <a href="#/signin">Sign in</a>.
      </p>
    </AuthShell>
  );
}

export function ResetPassword() {
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);

  const submit = useAction(async () => {
    const query = window.location.hash.split("?")[1] ?? "";
    const token = new URLSearchParams(query).get("token") ?? "";
    await api.post("/auth/reset", { token, new_password: password });
    setDone(true);
  });

  if (done) {
    return (
      <AuthShell kicker="Done" title="Password reset">
        <p className="muted" style={{ marginBottom: "1.75rem" }}>
          Every device is signed out. Sign in with the new password.
        </p>
        <a href="#/signin">
          <button>Sign in</button>
        </a>
      </AuthShell>
    );
  }

  return (
    <AuthShell kicker="Almost there" title="Choose a new password">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit.run();
        }}
      >
        <Field label="New password" hint="At least 12 characters. Longer beats complicated.">
          <input
            type="password"
            value={password}
            autoComplete="new-password"
            required
            autoFocus
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Error>{submit.error}</Error>
        <button type="submit" disabled={submit.busy || !password}>
          {submit.busy ? "Setting…" : "Set password"}
        </button>
      </form>
      <p className="facts" style={{ marginTop: "2rem" }}>
        Link expired? <a href="#/forgot">Request another</a>.
      </p>
    </AuthShell>
  );
}
