/** "Skip" and "Mark as applied", kept in this browser until the server has them: a lost connection, a
 * refresh or a phone switched off loses neither, and a job marked is never shown again. */
import { ApiError, api } from "./api";

export type Mark = "applied" | "hidden";
type Waiting = { job: number; mark: Mark; on: boolean };

const KEY = "job-marks";

function waiting(): Waiting[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "[]") as Waiting[];
  } catch {
    return [];
  }
}

function keep(marks: Waiting[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(marks));
  } catch {
    // Storage refused (a private window): the mark is still sent now, only not kept for later.
  }
}

/** Jobs marked here that the server has not confirmed yet: off the page whatever it answers. */
export function unsent(): Set<number> {
  return new Set(waiting().filter((each) => each.on).map((each) => each.job));
}

/** Record a mark, or take one back, and send it with whatever else is waiting. */
export function mark(job: number, kind: Mark, on: boolean): Promise<void> {
  keep([...waiting().filter((each) => each.job !== job || each.mark !== kind), { job, mark: kind, on }]);
  return send();
}

let sending: Promise<void> | null = null;

/** Send what is waiting, oldest first, until it is sent or the connection is down. */
export function send(): Promise<void> {
  sending ??= (async () => {
    for (let next = waiting()[0]; next; next = waiting()[0]) {
      const path = `/jobs/${next.job}/${next.mark}`;
      try {
        await (next.on ? api.put(path) : api.del(path));
      } catch (caught) {
        // Offline, the server down, or signed out: kept, and sent when it can be.
        if (!(caught instanceof ApiError) || caught.status >= 500 || caught.status === 401) return;
        // Refused for good (the job is gone): there is nothing to keep it for.
      }
      // Unless it was changed meanwhile: an Undo while this went out is still to be sent.
      const done = next;
      keep(waiting().filter((each) => each.job !== done.job || each.mark !== done.mark || each.on !== done.on));
    }
  })().finally(() => {
    sending = null;
  });
  return sending;
}

/** Forgotten on sign-out, or the next account would send this one's marks. */
export function forgetMarks(): void {
  keep([]);
}

window.addEventListener("online", () => void send());
