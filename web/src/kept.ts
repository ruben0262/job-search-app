/** What each page last read from the server, kept while the site is open: a page comes back drawn at
 * once, as it was, and is brought up to date behind that. Without it every tab drew empty and filled a
 * moment later (owner, 2026-11-03). The one cache in the site, keyed by address, and forgotten on
 * sign-out, or the next account would see this one's. */
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";

const kept = new Map<string, unknown>();
// Told when a path is read again, so every page drawing it draws the new answer: a plan's limits read
// as a document is ready show in Settings too (owner, 2026-11-19).
const fresh = new EventTarget();
// The reads on their way, by address: whoever asks for one of them gets its answer, not a request of their
// own. A page's read and the read-ahead of the same address went out side by side, and an account's page
// once for every time the pointer came back to its row.
const asking = new Map<string, Promise<unknown>>();
// How many times a page changed each address's answer itself (`set`): an answer asked for before a change is
// older than the change, and is dropped when it comes.
const changes = new Map<string, number>();

function ask<T>(path: string): Promise<T> {
  const underWay = asking.get(path) as Promise<T> | undefined;
  if (underWay) return underWay;
  const answer = api.get<T>(path).finally(() => asking.delete(path));
  asking.set(path, answer);
  return answer;
}

/** The last answer from `path`, if there is one. */
export function peek<T>(path: string): T | null {
  return (kept.get(path) as T | undefined) ?? null;
}

export function keep<T>(path: string, value: T): void {
  kept.set(path, value);
}

/** `path`'s last answer at once, and a fresh one as soon as it comes. `set` changes it here and in
 * what is kept, for a page that changed it itself; `reload` asks again. `first` is what must reach the
 * server before the answer is asked for, so the answer already has it. */
export function useKept<T>(path: string, first?: () => Promise<unknown>) {
  // What is drawn, with the address it was read from. Asked for another address, what is drawn is that
  // address's own last answer, never the one before's: an account's page drew the last account under the next
  // one's address, with switches that acted on the next one, until its read landed.
  const [held, setHeld] = useState(() => ({ path, data: peek<T>(path), failed: false }));
  const { data, failed } = held.path === path ? held : { data: peek<T>(path), failed: false };
  const drawn = useRef(data);
  drawn.current = data;
  // After `first`, the answer is asked for afresh: one already on its way was asked before `first` arrived.
  const reload = useCallback(() => {
    const before = changes.get(path);
    return (first ? first().then(() => api.get<T>(path)) : ask<T>(path)).then((answer) => {
      if (changes.get(path) !== before) return;
      kept.set(path, answer);
      fresh.dispatchEvent(new Event(path));
    }).catch(() => setHeld({ path, data: peek<T>(path), failed: true }));
  }, [path, first]);
  useEffect(() => {
    const again = () => setHeld({ path, data: peek<T>(path), failed: false });
    fresh.addEventListener(path, again);
    return () => fresh.removeEventListener(path, again);
  }, [path]);
  useEffect(() => {
    void reload();
  }, [reload]);
  // Every page drawing this address draws the change, this one among them: one page's change stayed its own,
  // and another part of the same screen went on showing what was there before.
  const set = useCallback((next: (now: T | null) => T | null) => {
    drawn.current = next(drawn.current);
    kept.set(path, drawn.current);
    changes.set(path, (changes.get(path) ?? 0) + 1);
    fresh.dispatchEvent(new Event(path));
  }, [path]);
  return { data, set, reload, failed };
}

/** Each page's first read, asked once somebody is signed in, so the first visit to a tab is as quick
 * as the next. */
export function readAhead(paths: string[]): void {
  for (const path of paths) {
    if (!kept.has(path)) ask(path).then((answer) => kept.set(path, answer)).catch(() => undefined);
  }
}

export function forgetKept(): void {
  kept.clear();
  changes.clear();
}
