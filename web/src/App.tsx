/**
 * Routing, and the one decision the whole app hangs off: where a signed-in user belongs.
 *
 * **The server decides, not the client.** `/dashboard` answers `stage: "onboarding"` with the page
 * list when signup is unfinished, so this file never re-derives "are they done yet" from a pile of
 * booleans. That rule already exists once, in `libs/onboarding/state.py`, and a second copy here
 * would be the one that goes stale.
 *
 * A hash router rather than a dependency: this is a wizard plus three screens, and `react-router`
 * would be more configuration than routing. Hash rather than history so Cloudflare Pages serves
 * `index.html` for every URL without a rewrite rule anyone has to remember.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, api, type Dashboard } from "./api";
import { Landing } from "./landing/Landing";
import { AdminPage } from "./pages/AdminPage";
import { AdminUserPage } from "./pages/AdminUserPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DataPage } from "./pages/DataPage";
import { clearFeedCache } from "./pages/FeedPage";
import { ModelPage } from "./pages/ModelPage";
import { clearAccountCache } from "./pages/SettingsPage";
import { clearConversationCache } from "./pages/SetupPage";
import { FeedPage } from "./pages/FeedPage";
import { Onboarding } from "./pages/Onboarding";
import { PrivacyPage } from "./pages/PrivacyPage";
import { ProcessPage } from "./pages/ProcessPage";
import { ForgotPassword, ResetPassword } from "./pages/ResetPassword";
import { SetupPage } from "./pages/SetupPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SignIn } from "./pages/SignIn";
import { Verify } from "./pages/Verify";
import { NotificationBell } from "./notifications";
import { ThemeToggle } from "./theme";

function useHash(): string {
  const [hash, setHash] = useState(() => window.location.hash.slice(1) || "/");
  useEffect(() => {
    const update = () => setHash(window.location.hash.slice(1) || "/");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return hash;
}

export function navigate(path: string): void {
  window.location.hash = path;
}

type Session = { state: "loading" } | { state: "out" } | { state: "in"; dashboard: Dashboard };

export function App() {
  const hash = useHash();
  const [session, setSession] = useState<Session>({ state: "loading" });

  const reload = useCallback(async () => {
    try {
      setSession({ state: "in", dashboard: await api.get<Dashboard>("/dashboard") });
    } catch (error) {
      // 401 is the ordinary signed-out case — `request` has already tried a refresh. Anything else
      // is a real failure and must not be disguised as a logout, or an outage looks like a bug in
      // the user's own session.
      if (error instanceof ApiError && error.status === 401) {
        // The page caches die with the session, or the next account to sign in on this browser
        // flashes this one's jobs and details.
        clearFeedCache();
        clearConversationCache();
        clearAccountCache();
        setSession({ state: "out" });
      } else throw error;
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Public, and reachable signed out: signup links it before anybody has an account, and the two
  // email links must work whatever session state the browser happens to be in.
  if (hash.startsWith("/privacy")) return <PrivacyPage />;
  if (hash.startsWith("/verify")) return <Verify onDone={reload} />;
  if (hash.startsWith("/forgot")) return <ForgotPassword />;
  if (hash.startsWith("/reset")) return <ResetPassword onDone={reload} />;

  if (session.state === "loading") return <main className="centered">Loading…</main>;

  if (session.state === "out") {
    // The landing page is the ported `mahoraga-jobs-web` (decision reversed 2026-07-30: one
    // deployment, one origin, auth CTAs wired straight into the router). The auth screen keeps
    // its own routes so the hero buttons and old bookmarks both land somewhere sensible.
    if (hash.startsWith("/signin") || hash.startsWith("/signup")) {
      return <SignIn onSignedIn={reload} initialMode={hash.startsWith("/signup") ? "up" : "in"} />;
    }
    return <Landing />;
  }

  const { dashboard } = session;
  if (dashboard.stage === "onboarding") {
    // Deliberately not routable past: a half-configured account produces a bad feed and the user
    // concludes the product does not work rather than that they skipped a step.
    return <Onboarding state={dashboard.onboarding} onChanged={reload} />;
  }

  // Keyed on the route so the fade-up replays on navigation, not only on first load.
  const section = hash.startsWith("/feed")
    ? "feed"
    : hash.startsWith("/profile")
      ? "profile"
      : hash.startsWith("/model")
        ? "model"
        : hash.startsWith("/process")
          ? "process"
          : hash.startsWith("/data")
            ? "data"
            : hash.startsWith("/settings")
              ? "settings"
              : hash.startsWith("/admin")
                ? "admin"
                : "overview";

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <div className="app">
        <Nav current={hash} isAdmin={dashboard.is_admin === true} />
        <div className="page-in" key={section}>
          {section === "feed" ? (
            <FeedPage />
          ) : section === "profile" ? (
            <SetupPage onChanged={reload} />
          ) : section === "model" ? (
            <ModelPage dashboard={dashboard} onChanged={reload} />
          ) : section === "process" ? (
            <ProcessPage onChanged={reload} />
          ) : section === "data" ? (
            <DataPage onChanged={reload} />
          ) : section === "admin" ? (
            // `#/admin/users/<id>` is its own page, not a panel that unfolds inside the list.
            // A detail view rendered in place has no address to link, no back button, and puts the
            // thing you opened underneath the table you opened it from.
            hash.startsWith("/admin/users/") ? (
              <AdminUserPage userId={hash.slice("/admin/users/".length)} />
            ) : (
              <AdminPage />
            )
          ) : section === "settings" ? (
            <SettingsPage onSignedOut={reload} />
          ) : (
            <DashboardPage dashboard={dashboard} onChanged={reload} />
          )}
        </div>
      </div>
    </>
  );
}

function Nav({ current, isAdmin }: { current: string; isAdmin: boolean }) {
  const links = [
    ["/", "Overview"],
    ["/feed", "Jobs"],
    ["/profile", "Profile"],
    ["/model", "Model"],
    ["/data", "Your details"],
    ["/settings", "Settings"],
    // Only for whoever `ADMIN_EMAILS` names. The API refuses `/admin/*` regardless; this keeps the
    // link out of everybody else's navigation rather than out of their reach.
    ...(isAdmin ? ([["/admin", "Platform"]] as const) : []),
  ] as const;
  return (
    <nav>
      <a className="brand" href="#/">
        Mahoraga
      </a>
      {links.map(([path, label]) => (
        <a
          key={path}
          href={`#${path}`}
          className={path === "/" ? (current === "/" ? "on" : "") : current.startsWith(path) ? "on" : ""}
        >
          {label}
        </a>
      ))}
      <NotificationBell />
      <ThemeToggle />
    </nav>
  );
}
