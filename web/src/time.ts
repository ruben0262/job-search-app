/** How this platform says "when". One definition, because two would eventually disagree. */
import { useSyncExternalStore } from "react";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;

function parse(iso: string | null): Date | null {
  if (!iso) return null;
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null : at;
}

/**
 * How long ago, in the largest unit that still says something: "just now", "5m ago", "3h ago",
 * "2d ago", "6w ago" — then the date, because "63w ago" tells nobody anything. A clock ahead of
 * ours reads as "just now" rather than as a negative age.
 */
export function ago(iso: string | null, now: number = Date.now()): string {
  const at = parse(iso);
  if (!at) return "";
  const elapsed = now - at.getTime();
  if (elapsed < MINUTE) return "just now";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)}m ago`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)}h ago`;
  if (elapsed < WEEK) return `${Math.floor(elapsed / DAY)}d ago`;
  if (elapsed < 52 * WEEK) return `${Math.floor(elapsed / WEEK)}w ago`;
  return at.toLocaleDateString();
}

// One clock for every "… ago" on screen, so all of them move on together and a page left open stays
// right (owner, 2026-11-02). It wakes once a minute, on the minute, and when the tab is shown again,
// and only while something shows a time; a label re-renders only when its minute has changed. Nothing
// is asked of the server: each time is a moment it already sent.
const listeners = new Set<() => void>();
let timer = 0;

function tell(): void {
  for (const listener of listeners) listener();
}

function nextMinute(): void {
  timer = window.setTimeout(() => {
    tell();
    nextMinute();
  }, MINUTE - (Date.now() % MINUTE));
}

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) {
    nextMinute();
    document.addEventListener("visibilitychange", tell);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", tell);
    }
  };
}

/** Now, to the minute, for `ago`: the same for every label on the page, and kept current. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, () => {
    const now = Date.now();
    return now - (now % MINUTE);
  });
}
