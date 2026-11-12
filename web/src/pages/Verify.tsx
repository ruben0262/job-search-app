/** The email confirmation link lands here: `#/verify?token=…`. A working link signs you in, and one from the
 * sign-up page's Member choice (`&plan=member`) goes on to the payment form. */
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import { AuthShell, ResendLink, planAsked } from "./SignIn";

export function Verify({ onDone }: { onDone: () => void | Promise<void> }) {
  const [failed, setFailed] = useState<string | null>(null);
  // The token works exactly once, and StrictMode runs effects twice in development.
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    const query = window.location.hash.split("?")[1] ?? "";
    const token = new URLSearchParams(query).get("token");
    if (!token) {
      setFailed("That link is missing its token. Try the one in your email again.");
      return;
    }
    void api
      .post("/auth/verify", { token })
      // The session first, then the route, exactly as signing in does: straight into the app.
      .then(async () => {
        await onDone();
        window.location.hash = planAsked() === "member" ? "/upgrade" : "/";
      })
      .catch((error: unknown) => {
        setFailed(error instanceof ApiError ? error.detail : "That link did not work.");
      });
  }, [onDone]);

  if (failed === null) {
    return (
      <AuthShell title="Confirming…">
        <p className="muted">Checking your link.</p>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="That link did not work">
      <p className="muted" style={{ marginBottom: "1.75rem" }}>
        {failed}
      </p>
      <ResendLink email="" plan={planAsked()} />
    </AuthShell>
  );
}
