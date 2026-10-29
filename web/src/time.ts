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
