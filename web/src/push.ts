/** Browser notifications: register the service worker, ask, subscribe with the site's key and tell the
 * server. Only the plumbing; an alert asks for them when it is saved or switched on (`alerts.tsx`). */
import { api } from "./api";
import type { components } from "./api-types";
import { onApple } from "./install";

type PushKey = components["schemas"]["PushKey"];

/** Whether this browser can do it at all. An iPhone can only once the site is on its home screen. */
export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** Where this browser stands. "home-screen" is an iPhone or iPad, which can only once the site is added to its
 * home screen and opened from there; "blocked", one whose person said no, which only its own settings undo. */
export type PushState = "on" | "off" | "blocked" | "home-screen" | "unsupported";

export async function pushState(): Promise<PushState> {
  if (!pushSupported()) return onApple() ? "home-screen" : "unsupported";
  if (Notification.permission === "denied") return "blocked";
  const subscription = await (await navigator.serviceWorker.getRegistration())?.pushManager
    .getSubscription();
  return subscription && Notification.permission === "granted" ? "on" : "off";
}

/** Ask, subscribe and register this browser. False when it cannot, or the person said no. */
export async function enablePush(): Promise<boolean> {
  if (!pushSupported()) return false;
  const registration = await navigator.serviceWorker.register("/sw.js");
  if ((await Notification.requestPermission()) !== "granted") return false;
  const { key } = await api.get<PushKey>("/push/key");
  const subscription =
    (await registration.pushManager.getSubscription()) ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: fromBase64Url(key),
    }));
  await api.post("/push/subscribe", subscription.toJSON());
  return true;
}

/** One notification to this browser, sent by the server as an alert is, to see that it arrives. False when this
 * browser has no subscription any more. */
export async function testPush(): Promise<boolean> {
  const subscription = await (await navigator.serviceWorker.getRegistration())?.pushManager
    .getSubscription();
  if (!subscription) return false;
  await api.post("/push/test", { endpoint: subscription.endpoint });
  return true;
}

/** The key arrives base64url-encoded, and `subscribe` wants its bytes. */
function fromBase64Url(text: string): Uint8Array<ArrayBuffer> {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
}
