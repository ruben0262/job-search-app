/**
 * The email confirmation link lands here: `#/verify?token=…`.
 *
 * **Confirming does not sign you in.** The server deliberately does not issue a session from a link
 * that arrived by email, so this ends at the sign-in form rather than inside the account.
 */
import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import { ThemeToggle } from "../theme";
import { Card } from "../ui";

export function Verify({ onDone }: { onDone: () => void }) {
  const [message, setMessage] = useState("Confirming…");

  useEffect(() => {
    const query = window.location.hash.split("?")[1] ?? "";
    const token = new URLSearchParams(query).get("token");
    if (!token) {
      setMessage("That link is missing its token. Try the one in your email again.");
      return;
    }
    void api
      .post("/auth/verify", { token })
      .then(() => {
        setMessage("Confirmed. You can sign in now.");
        onDone();
      })
      .catch((error: unknown) => {
        setMessage(
          error instanceof ApiError ? error.detail : "That link did not work. Request another.",
        );
      });
  }, [onDone]);

  return (
    <main className="wizard">
      <ThemeToggle floating />
      <Card title="Email confirmation">
        <p>{message}</p>
        <a href="#/">
          <button>Go to sign in</button>
        </a>
      </Card>
    </main>
  );
}
