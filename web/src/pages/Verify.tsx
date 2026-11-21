/** The email confirmation link lands here: `/verify#token=…`, the token where no server sees it (`route.ts`). A working link signs you in, on to picking a plan;
 * one from a paid card on the landing page (`&plan=member`, `&plan=starter`) opens that page on its checkout. */
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import { AuthShell, ResendLink, planAsked } from "./SignIn";
import { go } from "../route";

export function Verify({ onDone }: { onDone: () => void | Promise<void> }) {
  const [failed, setFailed] = useState<string | null>(null);
  // The token works exactly once, and StrictMode runs effects twice in development.
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (!token) {
      setFailed("That link is missing its token. Try the one in your email again.");
      return;
    }
    void api
      .post("/auth/verify", { token })
      // The session first, then the route, exactly as signing in does: straight into the app.
      .then(async () => {
        await onDone();
        // In the link's place, not after it: the token works once, and Back led to it again, to say "That
        // link did not work" to somebody it had just signed in.
        go(planAsked() === "free" ? "/" : `/?plan=${planAsked()}`, true);
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
