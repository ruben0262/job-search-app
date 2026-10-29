/** Alerts on your phone, each one tap: Telegram through the platform's bot, or this browser's
 * notifications. Both are optional; the site works the same without them. */
import { useCallback, useEffect, useState } from "react";

import { ApiError, api, type Schema } from "./api";
import { disablePush, enablePush, pushState } from "./push";
import { Error, useAction } from "./ui";

/** Opens Telegram at the bot with a one-time code; pressing Start there connects this account. */
export function TelegramButton({ label = "Connect Telegram" }: { label?: string }) {
  const [error, setError] = useState<string | null>(null);
  const open = async () => {
    setError(null);
    // Opened inside the tap: a window opened after an await is one browsers block.
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;
    try {
      const { url } = await api.post<Schema<"TelegramLink">>("/telegram/link");
      if (tab) tab.location.href = url;
      else window.location.href = url;
    } catch (caught) {
      tab?.close();
      setError(caught instanceof ApiError ? caught.detail : "Telegram is not reachable right now.");
    }
  };
  return (
    <>
      <button type="button" className="secondary" onClick={() => void open()}>
        {label}
      </button>
      <Error>{error}</Error>
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

/** Both channels with where each stands, read again whenever the tab comes back into view: Start is
 * pressed in Telegram, not here. */
export function Alerts() {
  const [telegram, setTelegram] = useState<string | null>(null);
  const read = useCallback(() => {
    api.get<Schema<"AccountOut">>("/account").then((account) => setTelegram(account.telegram_status))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    read();
    window.addEventListener("focus", read);
    return () => window.removeEventListener("focus", read);
  }, [read]);
  const disconnect = useAction(async () => {
    await api.del("/telegram/link");
    read();
  });

  return (
    <div className="channels">
      <div className="channel">
        <span className="label">Telegram</span>
        {telegram === "connected" ? (
          <>
            <span className="small muted">Connected.</span>
            <button type="button" className="secondary small" disabled={disconnect.busy}
                    onClick={() => void disconnect.run()}>
              Disconnect
            </button>
            <Error>{disconnect.error}</Error>
          </>
        ) : telegram ? (
          <TelegramButton label={telegram === "broken" ? "Reconnect Telegram" : "Connect Telegram"} />
        ) : null}
      </div>
      <div className="channel">
        <span className="label">This browser</span>
        <PushButton />
      </div>
    </div>
  );
}
