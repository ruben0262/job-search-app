/** Routing, and the one decision the whole app hangs off: where a signed-in user belongs. */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, type Dashboard } from "./api";
import { HelpButton } from "./help";
import { Landing } from "./landing/Landing";
import { stopped } from "./leave";
import { forgetMarks } from "./marks";
import { ADMIN_READS, AdminPage, CORPUS_FIRST, LATENCY_FIRST } from "./pages/AdminPage";
import { forgetKept, readAhead } from "./kept";
import { ADDED, AddJobPage } from "./pages/AddJobPage";
import { AdminUserPage } from "./pages/AdminUserPage";
import { APPLIED, AppliedPage } from "./pages/AppliedPage";
import { DOCUMENTS, DocumentsPage, HISTORY, IDENTITY, SAVED } from "./pages/DocumentsPage";
import { JobsPage } from "./pages/JobsPage";
import { Onboarding } from "./pages/Onboarding";
import { PrivacyPage } from "./pages/PrivacyPage";
import { ForgotPassword, ResetPassword } from "./pages/ResetPassword";
import { ACCOUNT, CREDENTIALS, SettingsPage } from "./pages/SettingsPage";
import { SignIn } from "./pages/SignIn";
import { Verify } from "./pages/Verify";
import { REVIEW, WordsPage } from "./pages/WordsPage";
import { NotificationBell } from "./notifications";
import { PLACES } from "./places";
import { TEMPLATES } from "./templates";
import { ThemeToggle } from "./theme";
import { Brand } from "./wheel";

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

export function App() {
  const hash = useHash();
  const [session, setSession] = useState<Session>({ state: "loading" });

  const reload = useCallback(async () => {
    try {
      setSession({ state: "in", dashboard: await api.get<Dashboard>("/dashboard") });
    } catch (error) {
      // 401 is the ordinary signed-out case — `request` has already tried a refresh.
      if (error instanceof ApiError && error.status === 401) {
        // What the pages kept dies with the session.
        forgetKept();
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

  // Every other tab's first read, once, so the first visit to each draws at once too. Not the jobs
  // list: Jobs is where everybody lands, and it reads that itself (measured locally: 71 ms, the rest
  // under 10 ms each).
  const ready = session.state === "in" && session.dashboard.stage === "ready";
  const admin = ready && session.dashboard.is_admin;
  useEffect(() => {
    if (ready) readAhead([ADDED, APPLIED, REVIEW, DOCUMENTS, SAVED, HISTORY, IDENTITY, TEMPLATES, ACCOUNT, CREDENTIALS]);
  }, [ready]);
  // And Admin's, for whoever sees it: its first open draws at once too.
  useEffect(() => {
    if (admin) readAhead([...ADMIN_READS, CORPUS_FIRST, LATENCY_FIRST]);
  }, [admin]);

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
    // The landing page, for anybody signed out; sign in and sign up behind its buttons.
    if (hash.startsWith("/signin") || hash.startsWith("/signup")) {
      // Keyed by the address, so the address alone says which: going back from one to the other kept the
      // first one's title on the second one's page.
      const mode = hash.startsWith("/signup") ? "up" : "in";
      return <SignIn key={mode} onSignedIn={reload} mode={mode} />;
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
          <Brand href="#/" />
          {/* A row of links up top on a laptop; a tab bar along the bottom on a phone, each picture over its
              short name. */}
          <div className="places">
            {PLACES.filter(([key]) => key !== "admin" || dashboard.is_admin).map(([key, label, icon, short]) => (
              <a key={key} href={key === "jobs" ? "#/" : `#/${key}`} className={section === key ? "on" : ""}
                 aria-current={section === key ? "page" : undefined}>
                <svg viewBox="0 0 24 24" aria-hidden="true">{icon}</svg>
                <span className="place-name">{label}</span>
                <span className="place-short">{short}</span>
              </a>
            ))}
          </div>
          <div className="nav-icons">
            <HelpButton />
            <NotificationBell />
            <ThemeToggle />
          </div>
        </nav>
        {/* No fade between places: it began each one invisible, and switching tabs showed nothing for
            a moment (owner, 2026-11-03). Each page draws at once from what it read last (`kept.ts`). */}
        <main className={section === "admin" ? "admin" : undefined}>
          {path?.startsWith("/add") ? (
            // The second tab of Jobs, not a place of its own.
            <AddJobPage onChanged={reload} />
          ) : section === "applied" ? (
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
