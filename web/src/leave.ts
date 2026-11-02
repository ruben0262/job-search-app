/** A page with changes not saved asks before anybody leaves it (owner, 2026-11-02). One page at a time
 * asks: the one showing. A link is stopped before it goes, the back button is put back while the page
 * asks, and closing or refreshing the tab gets the browser's own question, the only one it allows. */

let ask: ((to: string) => void) | null = null;

/** Set while a page has changes not saved, with what to do when somebody tries to go to `to`; `null`
 * once it has none. */
export function askBeforeLeaving(handler: ((to: string) => void) | null): void {
  ask = handler;
}

/** Whether going to `to` was stopped so the page could ask first. */
export function stopped(to: string): boolean {
  if (!ask) return false;
  ask(to);
  return true;
}

/** Go, once the page has said yes. */
export function go(to: string): void {
  ask = null;
  window.location.hash = to;
}

document.addEventListener("click", (event) => {
  const link = (event.target as Element | null)?.closest?.("a[href^='#']");
  if (!link || !ask || event.defaultPrevented) return;
  event.preventDefault();
  stopped(link.getAttribute("href")!.slice(1) || "/");
}, true);

window.addEventListener("beforeunload", (event) => {
  if (!ask) return;
  event.preventDefault();
  // Older browsers ask only when this is set.
  event.returnValue = "";
});
