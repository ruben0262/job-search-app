// The service worker `src/push.ts` registers: it shows a job alert, and opens the job when tapped.

self.addEventListener("push", (event) => {
  const { title, body, url } = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(title || "A new job for you", { body, data: { url } }),
  );
});

// A tab of the site that is already open is brought forward and taken to the job; otherwise one opens.
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
