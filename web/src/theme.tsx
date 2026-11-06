/** Dark by default, light by choice. */
import { useState, type MouseEvent } from "react";
import { flushSync } from "react-dom";

const KEY = "theme";
type Choice = "light" | "dark";

export function ThemeToggle() {
  // `index.html` set it before the first paint, from the same key.
  const [choice, setChoice] = useState<Choice>(
    () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"));
  const next = choice === "dark" ? "light" : "dark";

  // The new theme grows from the button as a circle where the browser can draw one; at once elsewhere.
  const toggle = (event: MouseEvent<HTMLButtonElement>) => {
    const apply = () => {
      document.documentElement.dataset.theme = next;
      localStorage.setItem(KEY, next);
      flushSync(() => setChoice(next));
    };
    if (!document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      apply();
      return;
    }
    const box = event.currentTarget.getBoundingClientRect();
    const x = box.left + box.width / 2;
    const y = box.top + box.height / 2;
    const end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    void document.startViewTransition(apply).ready.then(() => document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${end}px at ${x}px ${y}px)`] },
      { duration: 650, easing: "cubic-bezier(.65, 0, .35, 1)", pseudoElement: "::view-transition-new(root)" }));
  };

  return (
    <button
      type="button"
      className="theme-toggle"
      // Both labels exist because the icon alone is ambiguous to a screen reader.
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      onClick={toggle}
    >
      {choice === "dark" ? <Sun /> : <Moon />}
    </button>
  );
}

/* Inline SVG rather than an emoji: an emoji renders as a colour image on most platforms. */

function Moon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
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
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
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
