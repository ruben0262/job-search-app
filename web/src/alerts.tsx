/** Alerts off the site: Telegram through the person's own bot, this browser's notifications, and a
 * daily email. All optional; the site works the same without them. */
import { useCallback, useEffect, useState } from "react";

import { api, type Schema } from "./api";
import { disablePush, enablePush, pushState } from "./push";
import { Error, useAction } from "./ui";

type Telegram = Schema<"TelegramState">;

/** How often the page asks whether Start has been pressed in their bot. */
const START_POLL_MS = 2000;

/** Telegram through their own bot: three steps, and their chat found by itself once they press Start
 * in it. Connected, it offers a test message and a way to disconnect. Folded away until opened, with
 * where it stands said on the fold: it is optional, and three steps read as a chore (owner,
 * 2026-11-02). Open by itself while it waits for Start, or when it has stopped working. */
export function TelegramSetup() {
  const [state, setState] = useState<Telegram | null>(null);
  const [token, setToken] = useState("");
  const [tested, setTested] = useState(false);
  useEffect(() => {
    api.get<Telegram>("/telegram").then(setState).catch(() => undefined);
  }, []);
  // Waiting for Start, which is pressed in Telegram: asked every couple of seconds, and on coming back.
  const waiting = state?.status === "waiting";
  useEffect(() => {
    if (!waiting) return;
    const look = () => void api.post<Telegram>("/telegram/check").then(setState).catch(() => undefined);
    const timer = window.setInterval(look, START_POLL_MS);
    window.addEventListener("focus", look);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", look);
    };
  }, [waiting]);
  const save = useAction(async () => {
    setState(await api.put<Telegram>("/telegram/bot", { token: token.trim() }));
    setToken("");
  });
  const test = useAction(async () => {
    setState(await api.post<Telegram>("/telegram/test"));
    setTested(true);
  });
  const forget = useAction(async () => {
    setState(await api.del<Telegram>("/telegram/bot"));
    setTested(false);
  });

  // Opened by itself when it needs them, and never closed by itself: finishing must not fold it away.
  const needed = state?.status === "broken" || waiting;
  const fold = useCallback((node: HTMLDetailsElement | null) => {
    if (node && needed) node.open = true;
  }, [needed]);

  if (!state) return null;
  const said = state.status === "connected" ? `On: @${state.bot}`
    : state.status === "broken" ? "Stopped working" : waiting ? "Waiting for you to press Start" : "Off";
  const connected = (
    <div className="telegram-on">
      <p className="small">
        {state.status === "broken"
          ? `Telegram stopped taking messages from @${state.bot}. Open it, press Start, then send a test.`
          : `Connected to @${state.bot}. New jobs arrive there.`}
      </p>
      <div className="row">
        <button type="button" className="secondary small" disabled={test.busy} onClick={() => void test.run()}>
          {test.busy ? "Sending…" : "Send a test"}
        </button>
        {state.status === "broken" && state.open_url ? (
          <a className="button secondary small" href={state.open_url} target="_blank" rel="noreferrer">
            Open @{state.bot}
          </a>
        ) : null}
        <button type="button" className="secondary small" disabled={forget.busy} onClick={() => void forget.run()}>
          Disconnect
        </button>
      </div>
      {tested && !test.error ? <p className="small muted" role="status">Sent. Check Telegram.</p> : null}
      <Error>{test.error ?? forget.error}</Error>
    </div>
  );
  const steps = (
    <ol className="telegram-steps">
      <li className={waiting ? "done" : ""}>
        <span className="step-number" aria-hidden="true">1</span>
        <div>
          <strong>Make your bot.</strong> In Telegram, open{" "}
          <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">@BotFather</a>, send{" "}
          <code>/newbot</code>, and give it any name. It replies with a token.
        </div>
      </li>
      <li className={waiting ? "done" : ""}>
        <span className="step-number" aria-hidden="true">2</span>
        <div>
          <strong>Paste the token here.</strong>
          {waiting ? (
            <p className="small muted">
              Saved: @{state.bot}.{" "}
              <button type="button" className="linklike small" onClick={() => void forget.run()}>Use another bot</button>
            </p>
          ) : (
            <form className="row" onSubmit={(event) => {
              event.preventDefault();
              if (token.trim()) void save.run();
            }}>
              <input aria-label="Bot token" placeholder="123456789:AAH…" value={token} autoComplete="off"
                     spellCheck={false} onChange={(event) => setToken(event.target.value)} />
              <button type="submit" className="secondary small" disabled={!token.trim() || save.busy}>
                {save.busy ? "Checking…" : "Save token"}
              </button>
            </form>
          )}
          <Error>{save.error}</Error>
        </div>
      </li>
      <li>
        <span className="step-number" aria-hidden="true">3</span>
        <div>
          <strong>Press Start in your bot.</strong>{" "}
          {waiting && state.open_url ? (
            <>
              <a className="button small" href={state.open_url} target="_blank" rel="noreferrer">Open @{state.bot}</a>
              <p className="small muted" role="status">Waiting for you to press Start…</p>
            </>
          ) : (
            <span className="small muted">After step 2, a button here opens it. We find your chat from that.</span>
          )}
        </div>
      </li>
    </ol>
  );
  return (
    <details ref={fold} className="fold-plain telegram">
      <summary>
        <span className="label">Telegram</span>
        <span className="small muted">{said}</span>
      </summary>
      {state.status === "connected" || state.status === "broken" ? connected : steps}
    </details>
  );
}

/** The daily email of new jobs: on or off. */
export function EmailAlerts() {
  const [on, setOn] = useState<boolean | null>(null);
  useEffect(() => {
    api.get<Schema<"AccountOut">>("/account").then((account) => setOn(account.email_alerts)).catch(() => undefined);
  }, []);
  const toggle = useAction(async (next: boolean) => {
    setOn((await api.patch<{ email_alerts: boolean }>("/account", { email_alerts: next })).email_alerts);
  });
  return (
    <>
      <label className="pick">
        <input type="checkbox" role="switch" checked={on === true} disabled={on === null || toggle.busy}
               onChange={(event) => void toggle.run(event.target.checked)} />
        <span>A summary of new jobs each morning</span>
      </label>
      <Error>{toggle.error}</Error>
    </>
  );
}

/** Notifications in this browser: on, off, or why they cannot be. */
export function PushButton() {
  const [state, setState] = useState<Awaited<ReturnType<typeof pushState>> | null>(null);
  const [refused, setRefused] = useState(false);
  useEffect(() => {
    void pushState().then(setState).catch(() => setState("unsupported"));
  }, []);
  const toggle = useAction(async () => {
    setRefused(false);
    if (state === "on") {
      await disablePush();
      setState("off");
    } else if (await enablePush()) setState("on");
    else setRefused(true);
  });

  if (state === "home-screen") {
    return (
      <p className="small muted">
        Add this site to your home screen first: tap Share, then Add to Home Screen, and open it from
        there.
      </p>
    );
  }
  if (state === "unsupported") return <p className="small muted">This browser cannot show notifications.</p>;
  return (
    <>
      <button type="button" className="secondary" disabled={state === null || toggle.busy}
              onClick={() => void toggle.run()}>
        {toggle.busy ? "One moment…" : state === "on" ? "Turn off notifications" : "Turn on notifications"}
      </button>
      {state === "on" ? <span className="small muted">On in this browser.</span> : null}
      {refused ? (
        <p className="small muted">Your browser said no. Allow notifications for this site in its settings.</p>
      ) : null}
      <Error>{toggle.error}</Error>
    </>
  );
}

/** Every channel, each with where it stands. */
/** Where the alerts are, for an account without a membership: they come with one (owner, 2026-11-12), and a
 * channel set up without it would wait in silence, with nothing to say why. A button, not a link in the sentence
 * (owner, 2026-11-13), and a quiet one: it sits under the page's own main button, as on onboarding's last step. */
export function AlertsForMembers() {
  return (
    <div className="row">
      <span className="small">Alerts come with a membership.</span>
      <a className="button secondary small" href="#/upgrade">Become a member</a>
    </div>
  );
}

export function Alerts() {
  return (
    <div className="channels">
      <div className="channel stacked">
        <TelegramSetup />
      </div>
      <div className="channel">
        <span className="label">This browser</span>
        <PushButton />
      </div>
      <div className="channel">
        <span className="label">Email</span>
        <EmailAlerts />
      </div>
    </div>
  );
}
