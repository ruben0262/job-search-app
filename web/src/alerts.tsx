/** Where an alert reaches somebody off the site, set up inside the alert itself (owner, 2026-11-19): this phone's
 * or browser's notifications, asked for as an alert that sends to them is saved or switched on, and Telegram through
 * their own bot. Each alert's switch is the only on and off. */
import { useCallback, useEffect, useState } from "react";

import { api, type Schema } from "./api";
import { openInstall, standalone, useInstallable } from "./install";
import { MemberLock } from "./membership";
import { enablePush, pushState, testPush, type PushState } from "./push";
import { Error, useAction } from "./ui";

type Telegram = Schema<"TelegramState">;

/** How often the page asks whether Start has been pressed in their bot. */
const START_POLL_MS = 2000;

/** This phone's or browser's notifications: where they stand, and a way to ask for them, which only asks when the
 * browser can and has not been answered yet. */
export function usePhone() {
  const [state, setState] = useState<PushState | null>(null);
  useEffect(() => {
    void pushState().then(setState).catch(() => setState("unsupported"));
  }, []);
  /** True once this phone is on: asked here, or already. Asked only in a tap, which a browser requires. */
  const allow = useCallback(async () => {
    if (state !== "off") return state === "on";
    const on = await enablePush();
    setState(on ? "on" : await pushState());
    return on;
  }, [state]);
  return { state, allow };
}

export type Phone = ReturnType<typeof usePhone>;

/** Under "Your phone or browser" in an alert: what this one will do, and a test once it is on. */
export function PhoneSetup({ phone }: { phone: Phone }) {
  const [tested, setTested] = useState(false);
  const test = useAction(async () => {
    setTested(false);
    setTested(await testPush());
  });
  const said = {
    on: "On for this phone.",
    off: "When you save, this phone asks to allow notifications.",
    blocked: "Notifications are blocked here. Allow them for this site in your browser's settings.",
    "home-screen": "On an iPhone, add this site to your Home Screen first, then open it from there.",
    unsupported: "This browser cannot show notifications. Open the site on your phone.",
  };
  if (!phone.state) return null;
  return (
    <div className="channel-setup">
      <p className="small muted">{said[phone.state]}</p>
      <div className="row">
        <InstallButton />
        {phone.state === "on" ? (
          <button type="button" className="secondary small" disabled={test.busy} onClick={() => void test.run()}>
            {test.busy ? "Sending…" : "Send a test"}
          </button>
        ) : null}
      </div>
      {tested && !test.error ? <p className="small muted" role="status">Sent. It should appear in a few seconds.</p>
        : null}
      <Error>{test.error}</Error>
    </div>
  );
}

/** Under "Telegram" in an alert: their own bot in three steps, their chat found by itself once they press Start
 * in it; connected, a test message and a way to disconnect. One bot for every alert of theirs that sends to
 * Telegram. No form of its own: it sits inside the alert's, and Enter in the token box saves the token. */
export function TelegramSetup({ onChanged }: { onChanged: () => void }) {
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
    const look = () => void api.post<Telegram>("/telegram/check").then((next) => {
      setState(next);
      if (next.status === "connected") onChanged();
    }).catch(() => undefined);
    const timer = window.setInterval(look, START_POLL_MS);
    window.addEventListener("focus", look);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", look);
    };
  }, [waiting, onChanged]);
  const save = useAction(async () => {
    setState(await api.put<Telegram>("/telegram/bot", { token: token.trim() }));
    setToken("");
  });
  const test = useAction(async () => {
    setState(await api.post<Telegram>("/telegram/test"));
    setTested(true);
    // A Telegram that had stopped and takes the test is working again, which the cards say.
    onChanged();
  });
  const forget = useAction(async () => {
    setState(await api.del<Telegram>("/telegram/bot"));
    setTested(false);
    onChanged();
  });

  if (!state) return null;
  if (state.status === "connected" || state.status === "broken") {
    return (
      <div className="channel-setup">
        <p className="small">
          {state.status === "broken"
            ? `Telegram stopped taking messages from @${state.bot}. Open it, press Start, then send a test.`
            : `Connected to @${state.bot}. Every alert that sends to Telegram uses it.`}
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
  }
  const keep = () => {
    if (token.trim()) void save.run();
  };
  return (
    <ol className="steps channel-setup">
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
            <div className="row">
              <input aria-label="Bot token" placeholder="123456789:AAH…" value={token} autoComplete="off"
                     spellCheck={false} onChange={(event) => setToken(event.target.value)}
                     onKeyDown={(event) => {
                       if (event.key === "Enter") {
                         event.preventDefault();
                         keep();
                       }
                     }} />
              <button type="button" className="secondary small" disabled={!token.trim() || save.busy} onClick={keep}>
                {save.busy ? "Checking…" : "Save token"}
              </button>
            </div>
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
}

/** Where the alerts are, for an account without a membership: they come with one (owner, 2026-11-12), and a
 * channel set up without it would wait in silence, with nothing to say why. A button, not a link in the sentence
 * (owner, 2026-11-13), and a quiet one: it sits under the page's own main button, as on onboarding's last step. */
export function AlertsForMembers() {
  return <MemberLock>Alerts come with a membership.</MemberLock>;
}

/** The site as an app: the sheet that says why and how (`install.tsx`), on a phone or wherever the browser
 * offers it; nothing once it is the app. */
function InstallButton() {
  const installable = useInstallable();
  if (standalone() || (!installable && !matchMedia("(pointer: coarse)").matches)) return null;
  return <button type="button" className="secondary small" onClick={openInstall}>Install the app</button>;
}
