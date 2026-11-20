/** Dark by default, light by choice. */
import { useSyncExternalStore, type MouseEvent } from "react";
import { flushSync } from "react-dom";

const KEY = "theme";
type Choice = "light" | "dark";

// Who draws the theme: told when it changes, so the circle and More's row never disagree. Each kept its own
// copy at first, and the circle switched to the theme the row had just switched to.
const drawers = new Set<() => void>();

/** The theme in use, and a switch to the other that grows it from what was tapped: the circle button, or More's
 * row on a phone (`more.tsx`). Read from the page itself, where `index.html` set it before the first paint. */
export function useTheme() {
  const choice = useSyncExternalStore<Choice>((drawn) => {
    drawers.add(drawn);
    return () => void drawers.delete(drawn);
  }, () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"));
  const next = choice === "dark" ? "light" : "dark";

  // The new theme grows from the button as a circle where the browser can draw one; at once elsewhere.
  const toggle = (event: MouseEvent<HTMLElement>) => {
    const apply = () => {
      document.documentElement.dataset.theme = next;
      try {
        localStorage.setItem(KEY, next);
      } catch {
        // Storage refused (a private window): switched for this visit. It threw here, and the switch stopped
        // half made, the page in the new theme and its button still offering it.
      }
      flushSync(() => drawers.forEach((drawn) => drawn()));
      paintBars();
    };
    if (!document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      apply();
      return;
    }
    const box = event.currentTarget.getBoundingClientRect();
    const x = box.left + box.width / 2;
    const y = box.top + box.height / 2;
    const end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    // The circle is the change: the colours' own fades (`styles.css`) repainted the whole page under it on every
    // frame, and on a phone a frame in twenty was late (2026-11-18). Held off until it has grown.
    const root = document.documentElement;
    root.classList.add("theming");
    const growing = document.startViewTransition(apply);
    void growing.ready.then(() => root.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${end}px at ${x}px ${y}px)`] },
      { duration: 650, easing: "cubic-bezier(.65, 0, .35, 1)", pseudoElement: "::view-transition-new(root)" }));
    const grown = () => root.classList.remove("theming");
    void growing.finished.then(grown, grown);
  };
  return { choice, next, toggle };
}

/** The phone's own bars (the browser's, or the status bar of the app once installed) in the page's background,
 * so the page runs to the edge of the screen: read from the stylesheet's `--bg`, never a second copy of it. */
export function paintBars(): void {
  const background = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
  if (background) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", background);
}

export function ThemeToggle() {
  const { choice, next, toggle } = useTheme();
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
