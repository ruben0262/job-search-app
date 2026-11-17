/** More, the last tab of a phone's bar (owner, 2026-11-17: "too many buttons on the nav bar on phones"): the
 * places used now and then, help and the theme, in a sheet over the page. A laptop's bar has them all. */
import { useState } from "react";
import { createPortal } from "react-dom";

import { HELP_ICON, HelpSheet } from "./help";
import { MORE_ICON, PLACES, TABS } from "./places";
import { useTheme } from "./theme";
import { Sheet } from "./ui";

const CHEVRON = <svg className="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="M9.5 6l6 6-6 6" /></svg>;
const MOON = "M20 13.4A8 8 0 1110.6 4a6.3 6.3 0 009.4 9.4z";

export function More({ admin, section }: { admin: boolean; section: string }) {
  const [open, setOpen] = useState<"more" | "help" | null>(null);
  const { choice, toggle } = useTheme();
  const places = PLACES.filter(([key]) => !TABS.includes(key) && (key !== "admin" || admin));
  // Lit while one of its places is open, as a tab is.
  const on = places.some(([key]) => key === section);
  return (
    <>
      <button type="button" className={on ? "more-tab on" : "more-tab"} aria-haspopup="dialog"
              aria-expanded={open === "more"} onClick={() => setOpen("more")}>
        <svg className="place-icon" viewBox="0 0 24 24" aria-hidden="true">{MORE_ICON}</svg>
        <span className="place-short">More</span>
      </button>
      {/* Out of the bar, under the page's body: inside it, the bar's own rules laid the rows out as tabs. */}
      {open === "more" ? createPortal(
        <Sheet title="More" onClose={() => setOpen(null)}>
          <div className="more-list">
            {places.map(([key, label, icon]) => (
              <a key={key} href={`#/${key}`} className="more-row" aria-current={key === section ? "page" : undefined}
                 onClick={() => setOpen(null)}>
                <svg viewBox="0 0 24 24" aria-hidden="true">{icon}</svg>
                <span>{label}</span>
                {CHEVRON}
              </a>
            ))}
            <button type="button" className="more-row" onClick={() => setOpen("help")}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d={HELP_ICON} /></svg>
              <span>Help and contact</span>
              {CHEVRON}
            </button>
            {/* The theme grows from the row, as it does from the circle on a laptop. */}
            <button type="button" className="more-row" role="switch" aria-checked={choice === "dark"}
                    onClick={toggle}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d={MOON} /></svg>
              <span>Dark mode</span>
              <i className="knob" aria-hidden="true" />
            </button>
          </div>
        </Sheet>, document.body,
      ) : null}
      {open === "help" ? createPortal(<HelpSheet onClose={() => setOpen(null)} />, document.body) : null}
    </>
  );
}
