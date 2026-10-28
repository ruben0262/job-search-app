/** Browser notifications: register the service worker, ask, subscribe with the site's key and tell the
 * server — and the reverse. Only the plumbing; the button that calls these belongs to a page. */
import { api } from "./api";
import type { components } from "./api-types";

type PushKey = components["schemas"]["PushKey"];

/** Whether this browser can do it at all. An iPhone can only once the site is on its home screen. */
export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
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

/** Stop notifications in this browser, at the server and here. */
export async function disablePush(): Promise<void> {
  if (!pushSupported()) return;
  const subscription = await (await navigator.serviceWorker.getRegistration())?.pushManager
    .getSubscription();
  if (!subscription) return;
  await api.del("/push/subscribe", { endpoint: subscription.endpoint });
  await subscription.unsubscribe();
}

/** The key arrives base64url-encoded, and `subscribe` wants its bytes. */
function fromBase64Url(text: string): Uint8Array<ArrayBuffer> {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
}
