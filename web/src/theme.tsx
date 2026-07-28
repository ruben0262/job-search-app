/**
 * Light and dark, chosen or inherited.
 *
 * **Three states, not two.** `light`, `dark`, and *unset* — and unset is the default, because a
 * viewer whose system is dark should get dark without being asked. Pressing the toggle moves them
 * out of unset permanently; there is deliberately no way back to "follow the system", since a
 * three-way control is a puzzle in a corner nobody wanted to solve.
 *
 * The attribute is written to `<html>` **before first paint** by a snippet in `index.html`, not
 * here: a React effect runs after the first frame, so a dark-mode viewer would see one white flash
 * on every load. This module only keeps the toggle in sync afterwards.
 */
import { useEffect, useState } from "react";

const KEY = "theme";
type Choice = "light" | "dark";

function stored(): Choice | null {
  const value = localStorage.getItem(KEY);
  return value === "light" || value === "dark" ? value : null;
}

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ThemeToggle({ floating = false }: { floating?: boolean }) {
  const [choice, setChoice] = useState<Choice>(() => stored() ?? (systemPrefersDark() ? "dark" : "light"));

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", choice);
    localStorage.setItem(KEY, choice);
  }, [choice]);

  // Follow the system until the viewer decides for themselves. Once `localStorage` holds a choice
  // this stops mattering, which is the intended asymmetry.
  useEffect(() => {
    if (stored()) return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = (event: MediaQueryListEvent) => setChoice(event.matches ? "dark" : "light");
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, []);

  const next = choice === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className={floating ? "theme-toggle floating" : "theme-toggle"}
      // Both labels exist because the icon alone is ambiguous to a screen reader, and "dark mode"
      // does not say whether it is on or about to be.
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      onClick={() => setChoice(next)}
    >
      {choice === "dark" ? <Sun /> : <Moon />}
    </button>
  );
}

/* Inline SVG rather than an emoji: an emoji renders as a colour image on most platforms, which is
   the one thing this palette does not have. */

function Moon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Sun() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}
