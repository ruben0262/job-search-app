/** The bell. */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Schema } from "./api";
import { ago } from "./time";

type Feed = Schema<"NotificationList">;
type Notification = Feed["notifications"][number];

const POLL_MS = 60_000;

export function NotificationBell() {
  const [feed, setFeed] = useState<Feed>({ unread: 0, notifications: [] });
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api
      .get<Feed>("/notifications")
      .then((body) => {
        // Guarded because a dev proxy that misses this path answers with something that is not the feed.
        if (body && Array.isArray(body.notifications)) setFeed(body);
      })
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

  const clearAll = async () => {
    await api.post("/notifications/delete", { all: true }).catch(() => undefined);
    load();
  };

  // Dismissing one must not also open it: the × sits inside the row.
  const dismiss = async (event: React.MouseEvent, id: number) => {
    event.stopPropagation();
    await api.post("/notifications/delete", { ids: [id] }).catch(() => undefined);
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
            {/* Icons rather than words: two actions side by side as "Mark all read" and "Delete all". */}
            <div className="bell-actions">
              {feed.unread > 0 ? (
                <button
                  type="button"
                  className="bell-act"
                  aria-label="Mark all as read"
                  title="Mark all as read"
                  onClick={() => void markAll()}
                >
                  <EyeIcon />
                </button>
              ) : null}
              {feed.notifications.length > 0 ? (
                <button
                  type="button"
                  className="bell-act danger"
                  aria-label="Delete all notifications"
                  title="Delete all"
                  onClick={() => void clearAll()}
                >
                  <BinIcon />
                </button>
              ) : null}
            </div>
          </div>
          {feed.notifications.length === 0 ? (
            <p className="muted small" style={{ padding: "1rem 1.2rem" }}>
              Nothing yet. Anything worth knowing about your account lands here.
            </p>
          ) : (
            feed.notifications.map((item) => (
              // A row rather than one button: the dismiss control has to live inside the item.
              <div key={item.id} className={`bell-row${item.read ? "" : " unread"} ${item.level}`}>
                <button type="button" className="bell-item" onClick={() => void openOne(item)}>
                  <span className="bell-title">{item.title}</span>
                  {item.body ? <span className="bell-body">{item.body}</span> : null}
                  <span className="bell-when">{ago(item.created_at)}</span>
                </button>
                <button
                  type="button"
                  className="bell-x"
                  aria-label={`Dismiss: ${item.title}`}
                  title="Dismiss"
                  onClick={(event) => void dismiss(event, item.id)}
                >
                  <XIcon />
                </button>
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

function EyeIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="2.6" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

function BinIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 7h16" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path
        d="M9 7V5.5A1.5 1.5 0 0110.5 4h3A1.5 1.5 0 0115 5.5V7"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <path
        d="M6 7l.8 11.2A2 2 0 008.8 20h6.4a2 2 0 002-1.8L18 7"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function XIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
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
