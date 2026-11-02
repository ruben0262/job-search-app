/** Routing, and the one decision the whole app hangs off: where a signed-in user belongs. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, api, type Dashboard } from "./api";
import { HelpButton } from "./help";
import { Landing } from "./landing/Landing";
import { stopped } from "./leave";
import { forgetMarks } from "./marks";
import { AdminPage } from "./pages/AdminPage";
import { AdminUserPage } from "./pages/AdminUserPage";
import { AppliedPage } from "./pages/AppliedPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { JobsPage, clearJobsCache } from "./pages/JobsPage";
import { Onboarding } from "./pages/Onboarding";
import { PrivacyPage } from "./pages/PrivacyPage";
import { ForgotPassword, ResetPassword } from "./pages/ResetPassword";
import { SettingsPage, clearAccountCache } from "./pages/SettingsPage";
import { SignIn } from "./pages/SignIn";
import { Verify } from "./pages/Verify";
import { WordsPage } from "./pages/WordsPage";
import { NotificationBell } from "./notifications";
import { ThemeToggle } from "./theme";

function useHash(): string {
  const [hash, setHash] = useState(() => window.location.hash.slice(1) || "/");
  const shown = useRef(hash);
  useEffect(() => {
    const update = () => {
      const next = window.location.hash.slice(1) || "/";
      // The back button, while a page has changes not saved: put back while it asks (`leave.ts`).
      if (next !== shown.current && stopped(next)) {
        window.history.replaceState(null, "", `#${shown.current}`);
        return;
      }
      shown.current = next;
      setHash(next);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return hash;
}

type Session =
  | { state: "loading" } | { state: "down"; why: string } | { state: "out" } | { state: "in"; dashboard: Dashboard };

/** The places, in tab-bar order, Settings last. `#/feed` is Jobs too: every alert links there. Admin is
 * only listed for an admin; the API refuses `/admin/*` to anybody else on its own. */
const PLACES: [string, string, ReactNode][] = [
  ["jobs", "Jobs", <path key="i" d="M4 8h16v11H4zM9 8V5h6v3" />],
  ["applied", "Applied", <path key="i" d="M5 12.5l4.5 4.5L19 7.5" />],
  ["words", "My search", <path key="i" d="M4 12l8-8h7v7l-8 8zM15.5 8.5h.01" />],
  ["documents", "My documents", <path key="i" d="M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h6" />],
  ["admin", "Admin", <path key="i" d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z" />],
  ["settings", "Settings", <path key="i" d="M12 9a3 3 0 100 6 3 3 0 000-6zM4 12h2m12 0h2M12 4v2m0 12v2M6.3 6.3l1.4 1.4m8.6 8.6l1.4 1.4m0-11.4l-1.4 1.4m-8.6 8.6l-1.4 1.4" />],
];

export function App() {
  const hash = useHash();
  const [session, setSession] = useState<Session>({ state: "loading" });

  const reload = useCallback(async () => {
    try {
      setSession({ state: "in", dashboard: await api.get<Dashboard>("/dashboard") });
    } catch (error) {
      // 401 is the ordinary signed-out case — `request` has already tried a refresh.
      if (error instanceof ApiError && error.status === 401) {
        // The page caches die with the session.
        clearJobsCache();
        clearAccountCache();
        forgetMarks();
        setSession({ state: "out" });
        return;
      }
      // Before the first answer there is nothing else to show, so say so rather than "Loading…" forever.
      const why = error instanceof ApiError ? `The server answered ${error.status}.` : "No connection to the server.";
      setSession((now) => (now.state === "loading" ? { state: "down", why } : now));
      throw error;
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Telegram's Start is pressed outside this tab, so coming back is when the banners change.
  const signedIn = session.state === "in";
  useEffect(() => {
    if (!signedIn) return;
    const again = () => void reload();
    window.addEventListener("focus", again);
    return () => window.removeEventListener("focus", again);
  }, [signedIn, reload]);

  // Public, and reachable signed out: signup links it before anybody has an account.
  if (hash.startsWith("/privacy")) return <PrivacyPage />;
  if (hash.startsWith("/verify")) return <Verify onDone={reload} />;
  if (hash.startsWith("/forgot")) return <ForgotPassword />;
  if (hash.startsWith("/reset")) return <ResetPassword onDone={reload} />;

  if (session.state === "loading") return <main className="centered">Loading…</main>;
  if (session.state === "down") {
    return (
      <main className="centered">
        <p>Mahoraga can't be reached right now.</p>
        <p>{session.why}</p>
        <button type="button" onClick={() => { setSession({ state: "loading" }); void reload(); }}>
          Try again
        </button>
      </main>
    );
  }

  if (session.state === "out") {
    // The landing page is the ported `mahoraga-jobs-web` (decision reversed 2026-07-30: one deployment.
    if (hash.startsWith("/signin") || hash.startsWith("/signup")) {
      return <SignIn onSignedIn={reload} initialMode={hash.startsWith("/signup") ? "up" : "in"} />;
    }
    return <Landing />;
  }

  const { dashboard } = session;
  if (dashboard.stage === "onboarding") {
    // Deliberately not routable past: a half-configured account produces a bad feed and the user concludes.
    return <Onboarding state={dashboard.onboarding} onChanged={reload} />;
  }

  const [path, query = ""] = hash.split("?");
  const section = ["applied", "words", "documents", "admin", "settings"]
    .find((name) => path?.startsWith(`/${name}`)) ?? "jobs";
  const focus = Number(new URLSearchParams(query).get("job")) || null;

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <div className="app">
        <nav>
          <a className="brand" href="#/">
            Mahoraga
          </a>
          {/* A row of links up top on a laptop; a tab bar along the bottom on a phone. */}
          <div className="places">
            {PLACES.filter(([key]) => key !== "admin" || dashboard.is_admin).map(([key, label, icon]) => (
              <a key={key} href={key === "jobs" ? "#/" : `#/${key}`} className={section === key ? "on" : ""}
                 aria-current={section === key ? "page" : undefined}>
                <svg viewBox="0 0 24 24" aria-hidden="true">{icon}</svg>
                {label}
              </a>
            ))}
          </div>
          <div className="nav-icons">
            <HelpButton />
            <NotificationBell />
            <ThemeToggle />
          </div>
        </nav>
        {/* Keyed on the place so the fade-up replays on navigation, not only on first load. */}
        <main className={section === "admin" ? "page-in admin" : "page-in"} key={section}>
          {section === "applied" ? (
            <AppliedPage />
          ) : section === "words" ? (
            <WordsPage />
          ) : section === "documents" ? (
            <DocumentsPage />
          ) : section === "settings" ? (
            <SettingsPage dashboard={dashboard} onChanged={reload} onSignedOut={reload} />
          ) : section === "admin" ? (
            // `#/admin/users/<id>` is its own page, not a panel that unfolds inside the list.
            hash.startsWith("/admin/users/") ? (
              <AdminUserPage userId={hash.slice("/admin/users/".length)} />
            ) : (
              <AdminPage />
            )
          ) : (
            <JobsPage dashboard={dashboard} focus={focus} onChanged={reload} />
          )}
        </main>
      </div>
    </>
  );
}
