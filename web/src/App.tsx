/** Routing, and the one decision the whole app hangs off: where a signed-in user belongs. */
import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";

import { needsMore } from "./about";
import { ApiError, api, type Dashboard } from "./api";
import { DiscordButton, sayBackFromDiscord } from "./discord";
import { Making, forgetBuilds, resumeBuilds } from "./documents";
import { HelpButton } from "./help";
import { GetAppButton, InstallSheet, useAsked } from "./install";
import { forgetMarks } from "./marks";
import { MEMBERSHIP, PlanBar, backFromStripe } from "./membership";
import { forgetKept, readAhead } from "./kept";
import { ADDED, AddJobPage } from "./pages/AddJobPage";
import { ALERTS, AlertsPage } from "./pages/AlertsPage";
import { BOOKMARKED, BookmarksPage } from "./pages/BookmarksPage";
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
import { useRoute } from "./route";
import { markSignedIn, markSignedOut, onSessionChanged, wasSignedIn } from "./session";
import { readLimitsAhead } from "./aiaccounts";
import { TEMPLATES } from "./templates";
import { ThemeToggle } from "./theme";
import { Toasts } from "./toast";
import { Brand } from "./wheel";

// The landing page is a file of its own, for a visitor: somebody signed in was sent it on every visit, to a
// site whose pages they already had (owner, 2026-11-21). A browser nobody was signed in on asks for it at
// once, and sooner still from the page itself (`public/theme.js`), beside the site's script and not after it.
// Drawn the moment it is here, by hand and not through `lazy`: React holds a page it waited for back for
// some 300 ms, which the page that sells would have paid on every visit (measured: 3.0 s became 3.5).
type LandingPage = typeof import("./landing/Landing").Landing;
let Landing: LandingPage | null = null;
const landing = () => import("./landing/Landing").then((page) => (Landing = page.Landing));
if (!wasSignedIn()) void landing();

/** The landing page, once its file is here: nothing until then, which is a moment. */
function useLanding(wanted: boolean): LandingPage | null {
  const [page, setPage] = useState<LandingPage | null>(() => Landing);
  useEffect(() => {
    if (wanted && !page) void landing().then((loaded) => setPage(() => loaded));
  }, [wanted, page]);
  return page;
}

// Admin's pages come in a file of their own, to somebody who opens Admin and to nobody else: they were a sixth
// of what every visitor and every member downloaded to see a page they have no way into.
const admin = () => import("./pages/AdminPage");
const AdminPage = lazy(() => admin().then((pages) => ({ default: pages.AdminPage })));
const AdminUserPage = lazy(() => import("./pages/AdminUserPage").then((pages) => ({ default: pages.AdminUserPage })));

/** What the pages kept dies with the session: the next account must not be drawn this one's, or send its marks. */
function forget(): void {
  forgetKept();
  forgetMarks();
  forgetBuilds();
}

type Session =
  | { state: "loading" } | { state: "down"; why: string } | { state: "out" } | { state: "in"; dashboard: Dashboard };

export function App() {
  const route = useRoute();
  // A browser nobody was signed in on is a visitor's: the landing page at once, and the server asked behind
  // it. It was drawn only after two answers in a row, the page and then the sign-in refused (`session.ts`).
  const [session, setSession] = useState<Session>(() => (wasSignedIn() ? { state: "loading" } : { state: "out" }));
  // The Jobs list has its first answer: what the other tabs read ahead waits for it.
  const [fed, setFed] = useState(false);
  const onFed = useCallback(() => setFed(true), []);
  // Whether the server has said who this is, signed in or not, since the page was opened.
  const answered = useRef(false);

  const reload = useCallback(async () => {
    try {
      // Back from Stripe's form or portal: Stripe is read again first, so what they did there shows now,
      // not when its webhook arrives.
      if (backFromStripe()) await api.post("/billing/refresh").catch(() => undefined);
      sayBackFromDiscord();
      const dashboard = await api.get<Dashboard>("/dashboard");
      answered.current = true;
      markSignedIn();
      setSession({ state: "in", dashboard });
    } catch (error) {
      // 401 is the ordinary signed-out case — `request` has already tried a refresh.
      if (error instanceof ApiError && error.status === 401) {
        answered.current = true;
        // What the pages kept dies with the session.
        forget();
        markSignedOut();
        setFed(false);
        setSession({ state: "out" });
        return;
      }
      // Before the first answer there is nothing else to show, so say so rather than "Loading…" forever.
      const why = error instanceof ApiError ? `The server answered ${error.status}.` : "No connection to the server.";
      // And over the landing page drawn on a guess, when the server has never answered: its buttons would lead
      // nowhere.
      setSession((now) => (now.state === "loading" || !answered.current ? { state: "down", why } : now));
      throw error;
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // A session that begins here, by signing in or by a confirmation link: whatever this tab kept was somebody
  // else's, and the other tabs are told. A link opened where another account was signed in drew that account's
  // pages for the new one.
  const begin = useCallback(() => {
    forget();
    markSignedIn(true);
    return reload();
  }, [reload]);
  // And one that began, ended or changed hands in another tab: nothing kept here is drawn again, and the
  // server is asked who this is now.
  useEffect(() => onSessionChanged(() => {
    forget();
    setSession({ state: "loading" });
    void reload().catch(() => undefined);
  }), [reload]);

  // Telegram's Start is pressed outside this tab, so coming back is when the banners change.
  const signedIn = session.state === "in";
  useEffect(() => {
    if (!signedIn) return;
    const again = () => void reload();
    window.addEventListener("focus", again);
    return () => window.removeEventListener("focus", again);
  }, [signedIn, reload]);

  const Visitors = useLanding(session.state === "out");
  const [path = "/", query = ""] = route.split("?");
  // The place the address is in: its first word. `/`, `/jobs` and anything unknown are Jobs.
  const section = ["applied", "words", "alerts", "documents", "admin", "settings"]
    .find((name) => path === `/${name}` || path.startsWith(`/${name}/`)) ?? "jobs";
  // Every other tab's first read, once, so the first visit to each draws at once too. Not the jobs list: Jobs
  // is where everybody lands, and it reads that itself. After its first answer, not beside it: the list was the
  // last of some eighteen requests to leave, behind reads that open a hundred postings or parse a CV.
  const ready = session.state === "in" && session.dashboard.stage === "ready";
  const listing = section === "jobs" && !["/add", "/bookmarks", "/upgrade"].some((other) => path.startsWith(other));
  const ahead = ready && (fed || !listing);
  const isAdmin = session.state === "in" && session.dashboard.stage === "ready" && session.dashboard.is_admin;
  // Whether to ask this phone to install the app (`install.tsx`): here, before any page returns, as a hook is.
  const asked = useAsked(session.state === "in" && session.dashboard.stage === "ready" ? session.dashboard : null);
  useEffect(() => {
    if (ahead) {
      readAhead([ADDED, BOOKMARKED, APPLIED, REVIEW, ALERTS, DOCUMENTS, SAVED, HISTORY, IDENTITY, TEMPLATES, ACCOUNT,
                 MEMBERSHIP]);
      readLimitsAhead();
      // An admin's own pages too, so Admin opens as any other tab does.
      if (isAdmin) void admin();
    }
  }, [ahead, isAdmin]);
  useEffect(() => {
    // A CV still being made when the page was loaded again is said again, wherever they are.
    if (ready) resumeBuilds();
  }, [ready]);
  // Public, and reachable signed out: signup and the landing page's foot link them before anybody has an account.
  const legal = LEGAL_PAGES.find(([name]) => path === `/${name}`);
  if (legal) return <LegalPage name={legal[0]} />;
  if (path === "/verify") return <Verify onDone={begin} />;
  if (path === "/forgot") return <ForgotPassword />;
  if (path === "/reset") return <ResetPassword onDone={reload} />;

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
    if (path === "/signin" || path === "/signup") {
      // Keyed by the address, so the address alone says which: going back from one to the other kept the
      // first one's title on the second one's page.
      const mode = path === "/signup" ? "up" : "in";
      return <SignIn key={mode} onSignedIn={begin} mode={mode} />;
    }
    return Visitors ? <Visitors /> : null;
  }

  const { dashboard } = session;
  // Free or the membership, picked first, whatever the address (owner, 2026-11-12): nothing else until then.
  if (dashboard.stage === "plan") return <PlanPage onChanged={reload} />;
  if (path === "/upgrade") return <UpgradePage onChanged={reload} />;
  if (dashboard.stage === "onboarding") {
    // Deliberately not routable past: a half-configured account produces a bad feed and the user concludes.
    return <Onboarding state={dashboard.onboarding} areas={dashboard.areas} onChanged={reload} />;
  }

  const focus = Number(new URLSearchParams(query).get("job")) || null;
  // About you says too little: a red dot on My documents until it says enough (owner, 2026-11-22: "a
  // notification red circle next to the tab that needs attention").
  const asking = needsMore(dashboard.about);

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <div className="app">
        <nav>
          <Brand href="/jobs" />
          {/* A row of names up top on a laptop (owner, 2026-11-12). On a phone, an app's tab bar along the
              bottom: the four in `TABS`, each a picture over its short name, and More for the rest, help and the
              theme (owner, 2026-11-17). */}
          <div className="places">
            {PLACES.filter(([key]) => key !== "admin" || dashboard.is_admin).map(([key, label, icon, short]) => (
              <a key={key} href={`/${key}`}
                 // Admin's first reads, as the pointer reaches its link, so it opens drawn. They were asked
                 // for on every page an admin loaded, whichever page it was.
                 onPointerEnter={key === "admin" ? () => void admin().then((pages) => readAhead(pages.ADMIN_READS))
                   : undefined}
                 className={`${section === key ? "on" : ""}${TABS.includes(key) ? "" : " in-more"}`.trim()}
                 aria-current={section === key ? "page" : undefined}>
                <svg className="place-icon" viewBox="0 0 24 24" aria-hidden="true">{icon}</svg>
                <span className="place-name">{label}</span>
                <span className="place-short">{short}</span>
                {key === "documents" && asking ? (
                  <i className="place-dot" role="img" aria-label="About you needs more" />
                ) : null}
              </a>
            ))}
            <More admin={dashboard.is_admin} section={section} asked={asked} />
          </div>
          <div className="nav-icons">
            {asked ? <GetAppButton /> : null}
            <DiscordButton />
            <HelpButton />
            <NotificationBell about={dashboard.about} areas={dashboard.areas} />
            <ThemeToggle />
          </div>
        </nav>
        {/* No fade between places: it began each one invisible, and switching tabs showed nothing for
            a moment (owner, 2026-11-03). Each page draws at once from what it read last (`kept.ts`). */}
        <main className={section === "admin" ? "admin" : undefined}>
          {dashboard.plan === "member" ? null
            : <PlanBar plan={dashboard.plan} wait={dashboard.wait_hours} ahead={ahead} />}
          <Making />
          {path.startsWith("/add") ? (
            // A tab of Jobs, not a place of its own; and so is the next.
            <AddJobPage onChanged={reload} can={dashboard.areas.includes("add_job")}
                        starter={dashboard.plan === "starter"} />
          ) : path.startsWith("/bookmarks") ? (
            <BookmarksPage onChanged={reload} />
          ) : section === "applied" ? (
            <AppliedPage />
          ) : section === "words" ? (
            <WordsPage />
          ) : section === "alerts" ? (
            <AlertsPage onChanged={reload} query={query} />
          ) : section === "documents" ? (
            <DocumentsPage areas={dashboard.areas} about={dashboard.about} onChanged={reload}
                           focus={new URLSearchParams(query).has("about")} starter={dashboard.plan === "starter"} />
          ) : section === "settings" ? (
            <SettingsPage dashboard={dashboard} onSignedOut={reload} />
          ) : section === "admin" ? (
            <Suspense fallback={<p className="muted">Loading…</p>}>
              {/* `/admin/users/<id>` is its own page, not a panel that unfolds inside the list. */}
              {path.startsWith("/admin/users/") ? (
                <AdminUserPage userId={path.slice("/admin/users/".length)} />
              ) : (
                <AdminPage tab={path.slice("/admin/".length)} />
              )}
            </Suspense>
          ) : (
            <JobsPage dashboard={dashboard} focus={focus} onChanged={reload} onRead={onFed} />
          )}
        </main>
      </div>
      <Toasts />
      <InstallSheet asked={asked} />
    </>
  );
}
