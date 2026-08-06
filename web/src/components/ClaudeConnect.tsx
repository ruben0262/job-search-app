/** Connecting a Claude subscription with a button instead of a terminal. */
import { useEffect, useState } from "react";

import { api } from "../api";

type Phase = "idle" | "starting" | "waiting" | "finishing" | "done";

export function ClaudeConnect({ onConnected }: { onConnected: () => void }) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [session, setSession] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  // A live sign-in holds a real process on the server.
  useEffect(() => {
    if (!session) return;
    const drop = () => void api.post("/credentials/claude/oauth/cancel", { session_id: session });
    window.addEventListener("beforeunload", drop);
    return () => window.removeEventListener("beforeunload", drop);
  }, [session]);

  const begin = async () => {
    setError(null);
    setPhase("starting");
    setOpen(true);

    // Opened in the click, not after the await.
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;

    try {
      const body = await api.post<{ session_id: string; url: string }>(
        "/credentials/claude/oauth/start",
        {},
      );
      setSession(body.session_id);
      setUrl(body.url);
      setPhase("waiting");
      if (tab && !tab.closed) tab.location.replace(body.url);
    } catch (caught) {
      tab?.close();
      setPhase("idle");
      setOpen(false);
      setError(caught instanceof Error ? caught.message : "could not start the sign-in");
    }
  };

  const submit = async () => {
    if (!session || !code.trim()) return;
    setError(null);
    setPhase("finishing");
    try {
      await api.post("/credentials/claude/oauth/finish", { session_id: session, code: code.trim() });
      setPhase("done");
      // Held briefly so the confirmation is legible rather than a flash, then the caller advances.
      setTimeout(() => {
        setOpen(false);
        onConnected();
      }, 900);
    } catch (caught) {
      // The link stays.
      setPhase("waiting");
      setError(caught instanceof Error ? caught.message : "that code was not accepted");
    }
  };

  const close = () => {
    if (session) void api.post("/credentials/claude/oauth/cancel", { session_id: session });
    setOpen(false);
    setSession(null);
    setCode("");
    setPhase("idle");
    setError(null);
  };

  return (
    <>
      <button className="primary" disabled={phase === "starting"} onClick={() => void begin()}>
        {phase === "starting" ? "Opening Claude…" : "Connect my Claude account"}
      </button>
      {error && !open ? <p className="error">{error}</p> : null}

      {/* The backdrop does not close this one. */}
      {open ? (
        <div className="modal-backdrop">
          <div className="modal">
            {phase === "starting" ? (
              <>
                <h2>Opening Claude…</h2>
                <p className="small muted">
                  Getting your sign-in link ready. This takes a few seconds.
                </p>
              </>
            ) : phase === "done" ? (
              <>
                <h2>Connected</h2>
                <p className="small muted">
                  Your Claude account is stored and encrypted. Carrying on.
                </p>
              </>
            ) : (
              <>
                <h2>Finish signing in to Claude</h2>
                <p className="small muted">
                  A Claude tab opened. Approve the request there, and Claude will show you a short
                  code. Paste it below.
                </p>
                {url ? (
                  <p className="small">
                    Tab did not open?{" "}
                    <a href={url} target="_blank" rel="noopener noreferrer">
                      Open it here
                    </a>
                    .
                  </p>
                ) : (
                  <p className="small">
                    <button className="secondary" onClick={() => void begin()}>
                      Get a new link
                    </button>
                  </p>
                )}
                <label className="label" htmlFor="claude-code">
                  The code Claude showed you
                </label>
                <input
                  id="claude-code"
                  value={code}
                  autoFocus
                  autoComplete="off"
                  spellCheck={false}
                  disabled={phase === "finishing" || !url}
                  onChange={(event) => setCode(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void submit();
                    if (event.key === "Escape") close();
                  }}
                />
                {error ? <p className="error">{error}</p> : null}
                <div className="row">
                  <button className="secondary" onClick={close}>
                    Cancel
                  </button>
                  <button
                    className="primary"
                    disabled={phase === "finishing" || !code.trim() || !url}
                    onClick={() => void submit()}
                  >
                    {phase === "finishing" ? "Checking…" : "Connect"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}
