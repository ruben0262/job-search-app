/** Installing the site as an app (owner, 2026-11-17: "download it through chrome as a mini app"): on the home
 * screen with its own icon, opened in a window of its own, its notifications the app's. Chrome, Edge and Samsung
 * Internet offer it once, early, whatever page is open (`beforeinstallprompt`): kept here from the first moment,
 * so the Alerts page can offer it whenever it is opened. An iPhone or iPad offers nothing of the kind: Share,
 * then Add to Home Screen, which the Alerts page says in words (`onApple`). */
import { useSyncExternalStore } from "react";

type Offer = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> };

let offer: Offer | null = null;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((listener) => listener());

window.addEventListener("beforeinstallprompt", (event) => {
  // The Alerts page's button offers it, where the reason is said, rather than a bar over whichever page is open.
  event.preventDefault();
  offer = event as Offer;
  changed();
});
window.addEventListener("appinstalled", () => {
  offer = null;
  changed();
});

/** An iPhone or iPad, where a site reaches the home screen only through Share. An iPad says it is a Mac. */
export function onApple(): boolean {
  return /iPhone|iPad|iPod/.test(navigator.userAgent)
    || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
}

/** Whether this browser offers to install the site now: not when it is installed already, nor where it never
 * offers. */
export function useInstallable(): boolean {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => void listeners.delete(listener);
  }, () => offer !== null);
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
