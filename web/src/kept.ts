/** What each page last read from the server, kept while the site is open: a page comes back drawn at
 * once, as it was, and is brought up to date behind that. Without it every tab drew empty and filled a
 * moment later (owner, 2026-11-03). The one cache in the site, keyed by address, and forgotten on
 * sign-out, or the next account would see this one's. */
import { useCallback, useEffect, useState } from "react";

import { api } from "./api";

const kept = new Map<string, unknown>();

/** The last answer from `path`, if there is one. */
export function peek<T>(path: string): T | null {
  return (kept.get(path) as T | undefined) ?? null;
}

export function keep<T>(path: string, value: T): void {
  kept.set(path, value);
}

/** `path`'s last answer at once, and a fresh one as soon as it comes. `set` changes it here and in
 * what is kept, for a page that changed it itself; `reload` asks again. */
export function useKept<T>(path: string) {
  const [data, setData] = useState<T | null>(() => peek<T>(path));
  const [failed, setFailed] = useState(false);
  const reload = useCallback(() => api.get<T>(path).then((fresh) => {
    kept.set(path, fresh);
    setData(fresh);
    setFailed(false);
  }).catch(() => setFailed(true)), [path]);
  useEffect(() => {
    void reload();
  }, [reload]);
  const set = useCallback((next: (now: T | null) => T | null) => setData((now) => {
    const value = next(now);
    kept.set(path, value);
    return value;
  }), [path]);
  return { data, set, reload, failed };
}

/** Each page's first read, asked once somebody is signed in, so the first visit to a tab is as quick
 * as the next. */
export function readAhead(paths: string[]): void {
  for (const path of paths) {
    if (!kept.has(path)) api.get(path).then((fresh) => kept.set(path, fresh)).catch(() => undefined);
  }
}

export function forgetKept(): void {
  kept.clear();
}
