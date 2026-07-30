/**
 * The bell. Polls once a minute — every producer on this platform runs on a timer measured in
 * minutes, so a poll is indistinguishable from a push and carries no connection machinery
 * (the FlowSolutions original speaks websockets; that is the part deliberately not ported).
 *
 * Reading is explicit, not a side effect of opening the panel: "I glanced at the list" and "I have
 * dealt with this" are different claims, and an error that marks itself read on sight is an error
 * nobody remembers to fix.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";

type Notification = {
  id: number;
  event: string;
  level: "info" | "warning" | "error";
  title: string;
  body: string;
  url: string | null;
  created_at: string | null;
  read: boolean;
};

type Feed = { unread: number; notifications: Notification[] };

const POLL_MS = 60_000;

function ago(iso: string | null): string {
  if (!iso) return "";
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}

export function NotificationBell() {
  const [feed, setFeed] = useState<Feed>({ unread: 0, notifications: [] });
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api
      .get<Feed>("/notifications")
      .then(setFeed)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  // Clicking anywhere else closes the panel — the behaviour every dropdown is judged against.
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!panel.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const markAll = async () => {
    await api.post("/notifications/read", { all: true }).catch(() => undefined);
    load();
  };

  const openOne = async (item: Notification) => {
    if (!item.read) await api.post("/notifications/read", { ids: [item.id] }).catch(() => undefined);
    load();
    if (item.url) {
      setOpen(false);
      window.location.hash = item.url.replace(/^#/, "");
    }
  };

  return (
    <div className="bell-wrap" ref={panel}>
      <button
        type="button"
        className="bell"
        aria-label={feed.unread ? `${feed.unread} unread notifications` : "Notifications"}
        onClick={() => setOpen(!open)}
      >
        <BellIcon />
        {feed.unread > 0 ? <span className="bell-dot" aria-hidden="true" /> : null}
      </button>

      {open ? (
        <div className="bell-panel page-in" role="dialog" aria-label="Notifications">
          <div className="bell-head">
            <span className="kicker" style={{ margin: 0 }}>
              Notifications
            </span>
            {feed.unread > 0 ? (
              <button type="button" className="bell-mark" onClick={() => void markAll()}>
                Mark all read
              </button>
            ) : null}
          </div>
          {feed.notifications.length === 0 ? (
            <p className="muted small" style={{ padding: "1rem 1.2rem" }}>
              Nothing yet. Anything worth knowing about your account lands here.
            </p>
          ) : (
            feed.notifications.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`bell-item${item.read ? "" : " unread"} ${item.level}`}
                onClick={() => void openOne(item)}
              >
                <span className="bell-title">{item.title}</span>
                {item.body ? <span className="bell-body">{item.body}</span> : null}
                <span className="bell-when">{ago(item.created_at)}</span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

function BellIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M18 15.5V10a6 6 0 10-12 0v5.5L4.3 18h15.4L18 15.5z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <path d="M10 20.5a2.2 2.2 0 004 0" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}
