/** The email confirmation link lands here: `#/verify?token=…`. */
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import { AuthShell } from "./SignIn";

export function Verify({ onDone }: { onDone: () => void }) {
  const [state, setState] = useState<"pending" | "done" | "failed">("pending");
  const [detail, setDetail] = useState("");
  // The token works exactly once, and StrictMode runs effects twice in development.
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    const query = window.location.hash.split("?")[1] ?? "";
    const token = new URLSearchParams(query).get("token");
    if (!token) {
      setState("failed");
      setDetail("That link is missing its token. Try the one in your email again.");
      return;
    }
    void api
      .post("/auth/verify", { token })
      .then(() => {
        setState("done");
        onDone();
      })
      .catch((error: unknown) => {
        setState("failed");
        setDetail(
          error instanceof ApiError ? error.detail : "That link did not work. Request another.",
        );
      });
  }, [onDone]);

  if (state === "pending") {
    return (
      <AuthShell kicker="Almost there" title="Confirming…">
        <p className="muted">Checking your link.</p>
      </AuthShell>
    );
  }

  if (state === "done") {
    return (
      <AuthShell kicker="You're in" title="Email confirmed">
        <p className="muted" style={{ marginBottom: "1.75rem" }}>
          Your address is verified. Sign in and the setup takes it from there.
        </p>
        <a href="#/signin">
          <button>Sign in</button>
        </a>
      </AuthShell>
    );
  }

  return (
    <AuthShell kicker="Verification" title="That link did not work">
      <p className="muted" style={{ marginBottom: "1.75rem" }}>
        {detail}
      </p>
      <a href="#/signup">
        <button className="secondary">Request a new link</button>
      </a>
    </AuthShell>
  );
}
