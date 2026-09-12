/** How this platform says "when". One definition, because two would eventually disagree. */

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

/**
 * The clock time something happened: "14:32". Deliberately absolute — a stamp printed once and
 * left on screen for an hour has to still be true then, and only a relative one needs a ticker
 * to keep it honest. The age lives on the day heading and the tooltip instead.
 */
export function clock(iso: string | null): string {
  const at = parse(iso);
  return at ? at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

/** Whole calendar days between two moments, in the reader's own timezone. */
function daysApart(then: Date, now: Date): number {
  const a = new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime();
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((b - a) / DAY);
}

/**
 * The heading a day's messages sit under: "Today", "Yesterday", the weekday inside a week, the
 * date beyond it. This is where days and weeks are answered; the messages themselves carry only
 * the hour and minute.
 */
export function dayLabel(iso: string | null, now: number = Date.now()): string {
  const at = parse(iso);
  if (!at) return "";
  const today = new Date(now);
  const days = daysApart(at, today);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return at.toLocaleDateString([], { weekday: "long" });
  return at.toLocaleDateString([], {
    day: "numeric",
    month: "long",
    ...(at.getFullYear() === today.getFullYear() ? {} : { year: "numeric" }),
  });
}

/** The whole moment, for a tooltip: the exact answer behind the short one. */
export function full(iso: string | null): string {
  const at = parse(iso);
  return at ? at.toLocaleString() : "";
}
