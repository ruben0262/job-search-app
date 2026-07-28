/**
 * Sign in and sign up, on one screen.
 *
 * **Signup never says whether an address is already registered.** The server answers identically
 * either way, and this form has to match — a message here saying "that email is taken" would hand
 * back the account-existence oracle the API deliberately does not have.
 */
import { useState } from "react";

import { api } from "../api";
import { Card, Error, Field, useAction } from "../ui";
import { ThemeToggle } from "../theme";

export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [mode, setMode] = useState<"in" | "up">("in");
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
      <main className="wizard">
        <Card title="Check your email">
          <p>
            If that address can be registered, a confirmation link is on its way. The link is valid
            for a short time — request another if it expires.
          </p>
        </Card>
      </main>
    );
  }

  return (
    <main className="wizard">
      <ThemeToggle floating />
      <h1>{mode === "in" ? "Sign in" : "Create an account"}</h1>
      <p className="muted">
        Jobs found for you, scored by a model trained on your own judgement.{" "}
        <a href="#/privacy">What we do with your data</a>.
      </p>

      <Card>
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
          <div className="row">
            <button type="submit" disabled={submit.busy}>
              {submit.busy ? "Working…" : mode === "in" ? "Sign in" : "Create account"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => setMode(mode === "in" ? "up" : "in")}
            >
              {mode === "in" ? "I need an account" : "I already have one"}
            </button>
          </div>
        </form>
      </Card>
    </main>
  );
}
