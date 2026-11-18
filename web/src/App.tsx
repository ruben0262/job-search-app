/** Routing, and the one decision the whole app hangs off: where a signed-in user belongs. */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, type Dashboard } from "./api";
import { HelpButton } from "./help";
import { GetAppButton, InstallSheet, useAsked } from "./install";
import { Landing } from "./landing/Landing";
import { stopped } from "./leave";
import { forgetMarks } from "./marks";
import { MEMBERSHIP, backFromStripe } from "./membership";
import { ADMIN_READS, AdminPage, CORPUS_FIRST, LATENCY_FIRST } from "./pages/AdminPage";
import { forgetKept, readAhead } from "./kept";
import { ADDED, AddJobPage } from "./pages/AddJobPage";
import { ALERTS, AlertsPage } from "./pages/AlertsPage";
import { BOOKMARKED, BookmarksPage } from "./pages/BookmarksPage";
import { AdminUserPage } from "./pages/AdminUserPage";
import { APPLIED, AppliedPage } from "./pages/AppliedPage";
import { DOCUMENTS, DocumentsPage, HISTORY, IDENTITY, SAVED } from "./pages/DocumentsPage";
import { JobsPage } from "./pages/JobsPage";
import { Onboarding } from "./pages/Onboarding";
import { PlanPage } from "./pages/PlanPage";
import { LEGAL_PAGES, LegalPage } from "./pages/LegalPage";
import { ForgotPassword, ResetPassword } from "./pages/ResetPassword";
import { ACCOUNT, SettingsPage } from "./pages/SettingsPage";
import { SignIn } from "./pages/SignIn";
import { UpgradePage } from "./pages/UpgradePage";
import { Verify } from "./pages/Verify";
import { REVIEW, WordsPage } from "./pages/WordsPage";
import { NotificationBell } from "./notifications";
import { More } from "./more";
import { PLACES, TABS } from "./places";
import { AI_ACCOUNTS } from "./aiaccounts";
import { TEMPLATES } from "./templates";
import { ThemeToggle } from "./theme";
import { Toasts } from "./toast";
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
      // Back from Stripe's form or portal: Stripe is read again first, so what they did there shows now,
      // not when its webhook arrives.
      if (backFromStripe()) await api.post("/billing/refresh").catch(() => undefined);
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
  // Whether to ask this phone to install the app (`install.tsx`): here, before any page returns, as a hook is.
  const asked = useAsked(session.state === "in" && session.dashboard.stage === "ready" ? session.dashboard : null);
  const admin = session.state === "in" && session.dashboard.stage === "ready" && session.dashboard.is_admin;
  useEffect(() => {
    if (ready) {
      readAhead([ADDED, BOOKMARKED, APPLIED, REVIEW, ALERTS, DOCUMENTS, SAVED, HISTORY, IDENTITY, TEMPLATES, ACCOUNT,
                 AI_ACCOUNTS, MEMBERSHIP]);
    }
  }, [ready]);
  // And Admin's, for whoever sees it: its first open draws at once too.
  useEffect(() => {
    if (admin) readAhead([...ADMIN_READS, CORPUS_FIRST, LATENCY_FIRST]);
  }, [admin]);

  // Public, and reachable signed out: signup and the landing page's foot link them before anybody has an account.
  const legal = LEGAL_PAGES.find(([name]) => hash.startsWith(`/${name}`));
  if (legal) return <LegalPage name={legal[0]} />;
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
  // Free or the membership, picked first, whatever the address (owner, 2026-11-12): nothing else until then.
  if (dashboard.stage === "plan") return <PlanPage onChanged={reload} />;
  if (hash.startsWith("/upgrade")) return <UpgradePage onChanged={reload} />;
  if (dashboard.stage === "onboarding") {
    // Deliberately not routable past: a half-configured account produces a bad feed and the user concludes.
    return <Onboarding state={dashboard.onboarding} member={dashboard.areas.includes("alerts")}
                       scores={dashboard.areas.includes("match_scores")} onChanged={reload} />;
  }

  const [path, query = ""] = hash.split("?");
  const section = ["applied", "words", "alerts", "documents", "admin", "settings"]
    .find((name) => path?.startsWith(`/${name}`)) ?? "jobs";
  const focus = Number(new URLSearchParams(query).get("job")) || null;

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <div className="app">
        <nav>
          <Brand href="#/" />
          {/* A row of names up top on a laptop (owner, 2026-11-12). On a phone, an app's tab bar along the
              bottom: the four in `TABS`, each a picture over its short name, and More for the rest, help and the
              theme (owner, 2026-11-17). */}
          <div className="places">
            {PLACES.filter(([key]) => key !== "admin" || dashboard.is_admin).map(([key, label, icon, short]) => (
              <a key={key} href={key === "jobs" ? "#/" : `#/${key}`}
                 className={`${section === key ? "on" : ""}${TABS.includes(key) ? "" : " in-more"}`.trim()}
                 aria-current={section === key ? "page" : undefined}>
                <svg className="place-icon" viewBox="0 0 24 24" aria-hidden="true">{icon}</svg>
                <span className="place-name">{label}</span>
                <span className="place-short">{short}</span>
              </a>
            ))}
            <More admin={dashboard.is_admin} section={section} asked={asked} />
          </div>
          <div className="nav-icons">
            {asked ? <GetAppButton /> : null}
            <HelpButton />
            <NotificationBell />
            <ThemeToggle />
          </div>
        </nav>
        {/* No fade between places: it began each one invisible, and switching tabs showed nothing for
            a moment (owner, 2026-11-03). Each page draws at once from what it read last (`kept.ts`). */}
        <main className={section === "admin" ? "admin" : undefined}>
          {path?.startsWith("/add") ? (
            // A tab of Jobs, not a place of its own; and so is the next.
            <AddJobPage onChanged={reload} can={dashboard.areas.includes("add_job")} />
          ) : path?.startsWith("/bookmarks") ? (
            <BookmarksPage onChanged={reload} />
          ) : section === "applied" ? (
            <AppliedPage />
          ) : section === "words" ? (
            <WordsPage />
          ) : section === "alerts" ? (
            <AlertsPage onChanged={reload} query={query} />
          ) : section === "documents" ? (
            <DocumentsPage makes={dashboard.areas.includes("cvs")} />
          ) : section === "settings" ? (
            <SettingsPage dashboard={dashboard} onSignedOut={reload} />
          ) : section === "admin" ? (
            // `#/admin/users/<id>` is its own page, not a panel that unfolds inside the list.
            hash.startsWith("/admin/users/") ? (
              <AdminUserPage userId={hash.slice("/admin/users/".length)} />
            ) : (
              <AdminPage tab={path!.slice("/admin/".length)} />
            )
          ) : (
            <JobsPage dashboard={dashboard} focus={focus} onChanged={reload} />
          )}
        </main>
      </div>
      <Toasts />
      <InstallSheet asked={asked} />
    </>
  );
}
