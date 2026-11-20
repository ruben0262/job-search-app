/** Whether this browser was last signed in, kept where every tab of it can read: not the session, which is a
 * cookie no script sees, and not a secret. Two things hang off it. A visitor it has never seen signed in is
 * shown the landing page at once, where the page waited for two refusals in a row to learn the same. And a tab
 * hears when another signs in, out, or in as somebody else, so nothing one account read is drawn for the next. */
const KEY = "signed-in";

/** False only when this browser says nobody was. One that will not say (a private window) is asked the long way. */
export function wasSignedIn(): boolean {
  try {
    return localStorage.getItem(KEY) !== null;
  } catch {
    return true;
  }
}

/** Somebody is signed in. `began`: this moment, by signing in or by a confirmation link, which is said anew so
 * the other tabs hear it even when somebody was signed in before. */
export function markSignedIn(began = false): void {
  try {
    if (began || localStorage.getItem(KEY) === null) localStorage.setItem(KEY, String(Date.now()));
  } catch {
    // Storage refused: this tab works as before, and the others learn when they next ask the server.
  }
}

export function markSignedOut(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // As above.
  }
}

/** `then`, when another tab of this browser signed in or out. The browser tells every tab but the one that did it. */
export function onSessionChanged(then: () => void): () => void {
  const heard = (event: StorageEvent) => {
    if (event.key === KEY) then();
  };
  window.addEventListener("storage", heard);
  return () => window.removeEventListener("storage", heard);
}
