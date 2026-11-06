import type { ReactNode } from "react";

/** The places, in tab-bar order, Settings last. `#/feed` is Jobs too: every alert links there. Admin is
 * only listed for an admin; the API refuses `/admin/*` to anybody else on its own. On a phone the bar
 * shows the pictures alone (owner, 2026-11-06), so each is the thing its page holds: a briefcase of
 * jobs, a tick for applied, a magnifier for the search, a page, a shield, a gear. The landing page's
 * picture of the app draws the same bar. */
export const PLACES: [key: string, label: string, icon: ReactNode][] = [
  ["jobs", "Jobs", <path key="i" d="M5.5 7.5h13a2 2 0 012 2v8a2 2 0 01-2 2h-13a2 2 0 01-2-2v-8a2 2 0 012-2zM9 7.5v-2a1 1 0 011-1h4a1 1 0 011 1v2M3.5 12.5h17" />],
  ["applied", "Applied", <path key="i" d="M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM8.3 12.3l2.6 2.6 4.9-5.1" />],
  ["words", "My search", <path key="i" d="M10.5 4a6.5 6.5 0 110 13 6.5 6.5 0 010-13zM15.3 15.3L20 20" />],
  ["documents", "My documents", <path key="i" d="M7 3.5h6.5L18 8v12.5H7zM13.5 3.5V8H18M9.8 12.5h5M9.8 16h5" />],
  ["admin", "Admin", <path key="i" d="M12 3.5l7 2.8v5.2c0 4.3-2.9 7.6-7 9-4.1-1.4-7-4.7-7-9V6.3z" />],
  ["settings", "Settings", <path key="i" d="M10.16 5.35L10.56 2.91L13.44 2.91L13.84 5.35L15.40 5.99L17.41 4.56L19.44 6.59L18.01 8.60L18.65 10.16L21.09 10.56L21.09 13.44L18.65 13.84L18.01 15.40L19.44 17.41L17.41 19.44L15.40 18.01L13.84 18.65L13.44 21.09L10.56 21.09L10.16 18.65L8.60 18.01L6.59 19.44L4.56 17.41L5.99 15.40L5.35 13.84L2.91 13.44L2.91 10.56L5.35 10.16L5.99 8.60L4.56 6.59L6.59 4.56L8.60 5.99ZM15 12a3 3 0 11-6 0 3 3 0 016 0z" />],
];
