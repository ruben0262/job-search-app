// The service worker `src/push.ts` registers: it shows a job alert, and opens the job when tapped.

// In charge at once: a new version takes over without waiting for every tab to close, and the tab that turned
// notifications on is its own from then, so a tap can take that tab to the job (it cannot move a tab it does
// not control).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  const { title, body, url } = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(title || "A new job for you", {
      body,
      data: { url },
      // The app's icon beside it, and the wheel alone in a phone's status bar, where only its shape is drawn
      // (both `branding/build.py`'s).
      icon: "/icon-192.png",
      badge: "/badge-96.png",
    }),
  );
});

// A tab of the site that is already open is brought forward and taken to the job; otherwise one opens, in the
// installed app when there is one.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window" }).then((tabs) => {
      const tab = tabs.find((client) => "focus" in client);
      return tab ? tab.focus().then((open) => open.navigate(url)) : self.clients.openWindow(url);
    }),
  );
});
