/** Installing the site as an app (owner, 2026-11-17: "download it through chrome as a mini app"): on the home
 * screen with its own icon, opened in a window of its own, its notifications the app's. Chrome, Edge and Samsung
 * Internet offer it once, early, whatever page is open (`beforeinstallprompt`): kept here from the first moment.
 * An iPhone or iPad offers nothing of the kind: Share, then Add to Home Screen, which the sheet shows.
 *
 * Asked of a phone until it is done (owner, the same night: "we need to make sure its in front of users until
 * they do install it, perhaps when we tell them we show the benefits too"), for a plan whose alerts the app
 * carries: the sheet once a visit, saying why, and "Get the app" in the bar up top and in More whenever it is
 * wanted. Done is the app on this phone, never on the account: one installed and then deleted is asked for
 * again (owner, 2026-11-18). Opened as the app, nothing is asked; in a browser, Chrome on Android says whether
 * the app is on the phone (`related_applications` in the manifest). An iPhone's browser cannot tell, and asks. */
import { useEffect, useSyncExternalStore } from "react";

import type { Dashboard } from "./api";
import { toast } from "./toast";
import { Sheet, useAction } from "./ui";

type Offer = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> };

let offer: Offer | null = null;
// On this phone: installed from the browser's offer since this page loaded, or so said by the browser.
let installed = false;
let open = false;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((listener) => listener());

window.addEventListener("beforeinstallprompt", (event) => {
  // The sheet's button asks it, where the reason is said, rather than a bar of the browser's own.
  event.preventDefault();
  offer = event as Offer;
  changed();
});
window.addEventListener("appinstalled", () => {
  offer = null;
  installed = true;
  open = false;
  changed();
  toast({ text: "Mahoraga is on your home screen. Open it from there." });
});
// Where it can tell (Chrome on Android). Where it cannot, or refuses, the app is taken to be missing: asked.
void (navigator as Navigator & { getInstalledRelatedApps?: () => Promise<unknown[]> })
  .getInstalledRelatedApps?.()
  .then((apps) => {
    installed ||= apps.length > 0;
    changed();
  })
  .catch(() => undefined);

/** An iPhone or iPad, where a site reaches the home screen only through Share. An iPad says it is a Mac. */
export function onApple(): boolean {
  return /iPhone|iPad|iPod/.test(navigator.userAgent)
    || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
}

/** Opened from a home screen, in a window of its own: the app, installed and in use. */
export function standalone(): boolean {
  return matchMedia("(display-mode: standalone)").matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

function useKeptHere<T>(read: () => T): T {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => void listeners.delete(listener);
  }, read);
}

/** Whether this browser offers to install the site now: not when it is installed already, nor where it never
 * offers. */
export function useInstallable(): boolean {
  return useKeptHere(() => offer !== null);
}

/** Whether to ask this person, here: on a touch screen, with alerts in their plan, in a browser on a phone the
 * app is not known to be on. */
export function useAsked(dashboard: Extract<Dashboard, { stage: "ready" }> | null): boolean {
  const missing = !useKeptHere(() => installed);
  return missing && !standalone() && Boolean(dashboard?.areas.includes("alerts"))
    && matchMedia("(pointer: coarse)").matches;
}

/** The sheet, over whatever page is open: from "Get the app", or the Alerts page's button. */
export function openInstall(): void {
  open = true;
  changed();
}

/** The browser's own question, asked. An offer can be asked once: said yes or no, it is gone. */
export async function install(): Promise<void> {
  if (!offer) return;
  const asked = offer;
  offer = null;
  await asked.prompt();
  await asked.userChoice;
  changed();
}

// Said "not now" in this visit: the sheet waits for the next one. "Get the app" stays.
const LATER = "install-later";

/** "Get the app", up top on a phone until it is installed. */
export function GetAppButton() {
  return (
    <button type="button" className="get-app-pill" onClick={openInstall}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d={DOWN} /></svg>
      Get the app
    </button>
  );
}

/** The sheet: once a visit by itself while `asked`, and whenever "Get the app" is tapped. */
export function InstallSheet({ asked }: { asked: boolean }) {
  const shown = useKeptHere(() => open);
  useEffect(() => {
    if (!asked) return;
    try {
      if (sessionStorage.getItem(LATER)) return;
    } catch {
      // Storage refused (a private window): asked on every load, which is still not a page that cannot close.
    }
    // A moment after the page has drawn, so it rises over the jobs rather than in place of them.
    const timer = window.setTimeout(openInstall, 800);
    return () => clearTimeout(timer);
  }, [asked]);
  if (!shown) return null;
  const close = () => {
    open = false;
    changed();
    try {
      sessionStorage.setItem(LATER, "1");
    } catch {
      // As above: asked again next load.
    }
  };
  return <Sheet title="Get the Mahoraga app" onClose={close}><GetApp onClose={close} /></Sheet>;
}

/** Why, then how: the browser's own button where it offers one, else its steps, drawn as the phone draws them. */
function GetApp({ onClose }: { onClose: () => void }) {
  const installable = useInstallable();
  const add = useAction(install);
  const apple = onApple();
  return (
    <div className="get-app">
      <span className="get-app-icon" aria-hidden="true"><img src="/icon-192.png" alt="" /><b>1</b></span>
      <p className="lede">It sits on your home screen like any other app. Nothing to download from a store.</p>
      <ul className="get-app-why">
        <li>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={BELL} /></svg>
          <span><strong>Alerts on your lock screen</strong>
            Know the moment a job you want is posted.{apple ? " On an iPhone, they only arrive through the app." : ""}
          </span>
        </li>
        <li>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={TAP} /></svg>
          <span><strong>One tap to your jobs</strong>Straight from your home screen, no address to type.</span>
        </li>
        <li>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={FULL} /></svg>
          <span><strong>Full screen</strong>No browser bars, just your jobs.</span>
        </li>
      </ul>
      {installable ? (
        <button type="button" className="main" disabled={add.busy} onClick={() => void add.run()}>
          Install the app
        </button>
      ) : (
        <ol className="get-app-steps" aria-label="How to add it">
          {(apple ? APPLE : OTHER).map(([step, hint, picture]) => (
            <li key={step}>
              <span><strong>{step}</strong>{hint}</span>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d={picture} /></svg>
            </li>
          ))}
        </ol>
      )}
      <button type="button" className="linklike" onClick={onClose}>{installable ? "Not now" : "Got it"}</button>
    </div>
  );
}

// The pictures, each drawn as the phone or this site draws it.
export const DOWN = "M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14";
const BELL = "M6 16.5V11a6 6 0 0112 0v5.5l1.5 2h-15zM10 20.5a2 2 0 004 0";
const TAP = "M13 3L5 13.5h6L10 21l8-10.5h-6z";
const FULL = "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5";
const SHARE = "M12 3.5v11M8 7.5l4-4 4 4M7.5 10.5H5.5v10h13v-10h-2";
const ADD = "M6.5 4.5h11a2 2 0 012 2v11a2 2 0 01-2 2h-11a2 2 0 01-2-2v-11a2 2 0 012-2zM12 8.5v7M8.5 12h7";
const MENU = "M12 5.5h.01M12 12h.01M12 18.5h.01";
export const OPEN = "M9.5 4.5h-5v15h15v-5M13.5 4.5h6v6M19.5 4.5L11 13";

/** Safari's way, and Chrome's on an iPhone: Share, Add to Home Screen, then the app, signed in once there. */
const APPLE: [string, string, string][] = [
  ["Tap Share", " The square with an arrow, in your browser's bar or under ⋯.", SHARE],
  ["Tap Add to Home Screen", " Scroll down the list if it is not there.", ADD],
  ["Open Mahoraga from your home screen", " Sign in once there, and that is it.", OPEN],
];

/** Where the browser offers nothing by itself yet: its own menu. */
const OTHER: [string, string, string][] = [
  ["Tap your browser's menu", " The ⋮ at the top or the foot of the screen.", MENU],
  ["Tap Install app, or Add to Home screen", "", ADD],
  ["Open Mahoraga from your home screen", "", OPEN],
];
