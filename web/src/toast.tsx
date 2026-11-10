/** Pop-up notices, at the foot of the screen, that leave by themselves (owner, 2026-11-10). Any page
 * says one with `toast(...)`; `Toasts`, once in the app, shows them. One way for the whole site.
 *
 * Each is put in the browser's top layer (a popover) inside whichever dialog is open: the open job is a
 * dialog above everything, and a notice outside it would sit behind it with its links dead. */
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export type Toast = {
  text: ReactNode;
  /** Where to go about it: "Open Settings", "See them in My search". */
  links?: { href: string; label: string }[];
};

/** How long one stays: two lines read and a link reached. Paused while it is pointed at or focused. */
export const TOAST_MS = 8000;
/** The most on screen at once; a fourth takes the oldest's place. */
const MAX_SHOWN = 3;

type Shown = Toast & { id: number };
let shown: Shown[] = [];
let lastId = 0;
const listeners = new Set<() => void>();
const changed = (next: Shown[]) => {
  shown = next;
  listeners.forEach((listener) => listener());
};

export function toast(notice: Toast): void {
  changed([...shown.slice(1 - MAX_SHOWN), { ...notice, id: ++lastId }]);
}

const dismiss = (id: number) => changed(shown.filter((each) => each.id !== id));

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};

export function Toasts() {
  const list = useSyncExternalStore(subscribe, () => shown);
  if (!list.length) return null;
  return createPortal(<Stack list={list} />, openDialog() ?? document.body);
}

/** The dialog the person is in, if any: a modal one makes everything outside it inert. */
function openDialog(): Element | null {
  try {
    return document.querySelector("dialog:modal");
  } catch {
    return document.querySelector("dialog[open]");
  }
}

function Stack({ list }: { list: Shown[] }) {
  const stack = useRef<HTMLDivElement>(null);
  // Into the top layer before it is painted; where popovers are unknown it is a plain fixed box.
  useLayoutEffect(() => {
    const element = stack.current;
    if (element && !element.matches?.(":popover-open")) element.showPopover?.();
  }, []);
  return (
    <div ref={stack} className="toasts" popover="manual">
      {list.map((notice) => <Item key={notice.id} notice={notice} />)}
    </div>
  );
}

function Item({ notice }: { notice: Shown }) {
  const [held, setHeld] = useState(false);
  const left = useRef(TOAST_MS);
  useEffect(() => {
    if (held) return;
    const started = Date.now();
    const timer = window.setTimeout(() => dismiss(notice.id), left.current);
    return () => {
      window.clearTimeout(timer);
      left.current -= Date.now() - started;
    };
  }, [held, notice.id]);
  return (
    <div className={held ? "toast held" : "toast"} role="status"
         onMouseEnter={() => setHeld(true)} onMouseLeave={() => setHeld(false)}
         onFocus={() => setHeld(true)} onBlur={() => setHeld(false)}>
      <p>{notice.text}</p>
      {notice.links?.length ? (
        <div className="row">
          {notice.links.map((link, index) => (
            <a key={link.href} href={link.href} onClick={() => dismiss(notice.id)}
               className={index ? "button secondary small" : "button small"}>
              {link.label}
            </a>
          ))}
        </div>
      ) : null}
      <button type="button" className="toast-close" aria-label="Close" onClick={() => dismiss(notice.id)}>×</button>
      {/* The time it has left, running down; held still while it is held. */}
      <span className="toast-time" style={{ animationDuration: `${TOAST_MS}ms` }} aria-hidden="true" />
    </div>
  );
}
