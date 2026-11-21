/** Where somebody is on the site, which is the address itself: `/jobs`, `/applied`, `/alerts?alert=3`. Until
 * 2026-11-21 every address began `/#/` (owner: "instead of /#/ ... something more appropriate like /jobs or
 * /applied"). The server gives a browser the one page for any of them (`services/api/guard.py`), and this
 * draws the page the address names.
 *
 * A secret in an address goes after the `#`: the link in a confirmation or reset email is
 * `/verify#token=…`. That part never leaves the browser, so it is in no request, in no log on the way, and in
 * no address another site is told the visitor came from. */
import { useSyncExternalStore } from "react";

import { stopped } from "./leave";

/** An address from before, wherever it was kept (an email, a bookmark, an alert already sent, a notification
 * stored): read as the address it means. `#/feed?job=7` was Jobs. */
function modern(old: string): string {
  const [path = "/", query = ""] = old.slice(1).split("?");
  const to = path === "/feed" ? "/jobs" : path;
  if (new URLSearchParams(query).has("token")) return `${to}#${query}`;
  return query ? `${to}?${query}` : to;
}

/** An old address in the bar is put right where it stands, as the page loads and whenever it turns up after. */
function righted(): void {
  if (window.location.hash.startsWith("#/")) window.history.replaceState(null, "", modern(window.location.hash));
}
righted();

const drawn = new Set<() => void>();
const here = () => window.location.pathname + window.location.search;
/** The page shown: where the back button is put back to while a page asks about its unsaved changes. */
let shown = here();

function changed(): void {
  shown = here();
  drawn.forEach((draw) => draw());
}

/** Go to a page of the site. `replace`: in this one's place, so Back does not return to it. Another page
 * starts at its top: the address names no part of a page, and the browser left the new one scrolled as far
 * down as the old one was. Going back is the browser's own to put right, and is left to it. */
export function go(to: string, replace = false): void {
  const next = to.startsWith("#/") ? modern(to) : to;
  if (next !== shown && stopped(next)) return;
  const moved = next.split(/[?#]/)[0] !== window.location.pathname;
  window.history[replace ? "replaceState" : "pushState"](null, "", next);
  changed();
  if (moved) window.scrollTo(0, 0);
}

/** The address shown, as `/path?query`: every page draws from it and is drawn again when it changes. */
export function useRoute(): string {
  return useSyncExternalStore((draw) => {
    drawn.add(draw);
    return () => void drawn.delete(draw);
  }, here);
}

// The back and forward buttons. While a page has changes not saved, it is put back while the page asks
// (`leave.ts`).
window.addEventListener("popstate", () => {
  righted();
  if (here() !== shown && stopped(here())) {
    window.history.pushState(null, "", shown);
    return;
  }
  changed();
});

// A link to a page of the site is a change of page here, not a page fetched again. Left to the browser: a
// link that opens elsewhere or downloads (`target`, `download`), one to another site or to a place on this
// page (`#how`), and a click that asks for a new tab.
document.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey
      || event.altKey) return;
  const link = (event.target as Element | null)?.closest?.("a[href]");
  const href = link?.getAttribute("href") ?? "";
  if (!link || link.hasAttribute("target") || link.hasAttribute("download")) return;
  if (!(href.startsWith("/") && !href.startsWith("//")) && !href.startsWith("#/")) return;
  event.preventDefault();
  go(href);
});
