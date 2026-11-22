import type { ReactNode } from "react";

/** The places, in order, Settings last. `#/feed` is Jobs too: every alert links there. Admin is only listed
 * for an admin; the API refuses `/admin/*` to anybody else on its own. A laptop's bar is their names alone (owner,
 * 2026-11-12). A phone's is an app's (owner, 2026-11-17: "too many buttons"): the four in `TABS`, each a picture
 * over its short name, and More for the rest. The landing page's picture of the app draws the same bar. Each
 * picture is the thing its page holds (a briefcase of jobs, a tick for applied…); the Jobs page's Applied mark
 * and the landing page's dock use them too. */
export const PLACES: [key: string, label: string, icon: ReactNode, short: string][] = [
  ["jobs", "Jobs", <path key="i" d="M5.5 7.5h13a2 2 0 012 2v8a2 2 0 01-2 2h-13a2 2 0 01-2-2v-8a2 2 0 012-2zM9 7.5v-2a1 1 0 011-1h4a1 1 0 011 1v2M3.5 12.5h17" />, "Jobs"],
  ["applied", "Applied", <path key="i" d="M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM8.3 12.3l2.6 2.6 4.9-5.1" />, "Applied"],
  ["words", "My search", <path key="i" d="M10.5 4a6.5 6.5 0 110 13 6.5 6.5 0 010-13zM15.3 15.3L20 20" />, "Search"],
  ["documents", "My documents", <path key="i" d="M7 3.5h6.5L18 8v12.5H7zM13.5 3.5V8H18M9.8 12.5h5M9.8 16h5" />, "Docs"],
  ["alerts", "Alerts", <path key="i" d="M6 16.5V11a6 6 0 0112 0v5.5l1.5 2h-15zM10 20.5a2 2 0 004 0" />, "Alerts"],
  ["admin", "Admin", <path key="i" d="M12 3.5l7 2.8v5.2c0 4.3-2.9 7.6-7 9-4.1-1.4-7-4.7-7-9V6.3z" />, "Admin"],
  ["settings", "Settings", <path key="i" d="M10.16 5.35L10.56 2.91L13.44 2.91L13.84 5.35L15.40 5.99L17.41 4.56L19.44 6.59L18.01 8.60L18.65 10.16L21.09 10.56L21.09 13.44L18.65 13.84L18.01 15.40L19.44 17.41L17.41 19.44L15.40 18.01L13.84 18.65L13.44 21.09L10.56 21.09L10.16 18.65L8.60 18.01L6.59 19.44L4.56 17.41L5.99 15.40L5.35 13.84L2.91 13.44L2.91 10.56L5.35 10.16L5.99 8.60L4.56 6.59L6.59 4.56L8.60 5.99ZM15 12a3 3 0 11-6 0 3 3 0 016 0z" />, "Settings"],
];

/** A phone's tab bar, in order, before More: the places used every day (owner, 2026-11-17). The rest are in
 * More's sheet (`more.tsx`). */
export const TABS = ["jobs", "applied", "alerts", "documents"];

/** More's picture: three dots, as every app draws it, as big as a stroke of the other pictures is wide. */
export const MORE_ICON = <path d="M4 12a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0M10.5 12a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0M17 12a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0" />;
