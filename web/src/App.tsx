/** Routing, and the one decision the whole app hangs off: where a signed-in user belongs. */
import { useCallback, useEffect, useState } from "react";

import { ApiError, api, type Dashboard, type OnboardingState } from "./api";
import { Landing } from "./landing/Landing";
import { AdminPage } from "./pages/AdminPage";
import { AdminUserPage } from "./pages/AdminUserPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DataPage } from "./pages/DataPage";
import { clearFeedCache } from "./pages/FeedPage";
import { clearAccountCache } from "./pages/SettingsPage";
import { clearConversationCache } from "./pages/SetupPage";
import { FeedPage } from "./pages/FeedPage";
import { Onboarding } from "./pages/Onboarding";
import { PrivacyPage } from "./pages/PrivacyPage";
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
      // 401 is the ordinary signed-out case — `request` has already tried a refresh.
      if (error instanceof ApiError && error.status === 401) {
        // The page caches die with the session.
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

  // Public, and reachable signed out: signup links it before anybody has an account.
  if (hash.startsWith("/privacy")) return <PrivacyPage />;
  if (hash.startsWith("/verify")) return <Verify onDone={reload} />;
  if (hash.startsWith("/forgot")) return <ForgotPassword />;
  if (hash.startsWith("/reset")) return <ResetPassword onDone={reload} />;

  if (session.state === "loading") return <main className="centered">Loading…</main>;

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
    // Passed through untyped by `/dashboard`: what onboarding returns is onboarding's to define.
    return <Onboarding state={dashboard.onboarding as OnboardingState} onChanged={reload} />;
  }

  // Keyed on the route so the fade-up replays on navigation, not only on first load.
  const section = hash.startsWith("/feed")
    ? "feed"
    : hash.startsWith("/profile")
      ? "profile"
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
        <Nav current={hash} isAdmin={dashboard.is_admin} />
        <div className="page-in" key={section}>
          {section === "feed" ? (
            <FeedPage />
          ) : section === "profile" ? (
            <SetupPage onChanged={reload} />
          ) : section === "data" ? (
            <DataPage onChanged={reload} />
          ) : section === "admin" ? (
            // `#/admin/users/<id>` is its own page, not a panel that unfolds inside the list.
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
    ["/data", "Your details"],
    ["/settings", "Settings"],
    // Only for whoever `ADMIN_EMAILS` names.
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
