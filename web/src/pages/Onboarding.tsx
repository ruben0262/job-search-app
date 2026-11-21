/** Four screens: your CV, about you, what we found, done. Which one is the server's answer (`/dashboard`),
 * held back only so a person stays on a screen until they press its button. About you is the one screen the
 * server does not wait for: it can be skipped, so its screen says what it is for (owner, 2026-11-17). */
import { useState } from "react";

import { AlertsForMembers } from "../alerts";
import { api, type Schema } from "../api";
import { ThemeToggle } from "../theme";
import { Brand } from "../wheel";
import { Error, useAction } from "../ui";
import { AboutYou, Files, useDocuments } from "./DocumentsPage";
import { WordsForm } from "./WordsPage";
import { go } from "../route";

/** The four steps, as the bar over each screen names them. */
const STEPS = [["documents", "Your CV"], ["about", "About you"], ["review", "Your search"], ["done", "Done"]] as const;

/** What to write about, said before the box: three things a CV leaves out. */
const ASKS = ["The job you want next", "What you're best at", "What your CV doesn't say"];

export function Onboarding({ state, member, scores, onChanged }: {
  state: Schema<"OnboardingSteps">;
  /** Their plan has alerts (`libs.membership.AREAS`); without them, `AlertsForMembers`. */
  member: boolean;
  /** Their plan shows a match score on each job, which About you's words feed (`libs.fit`). */
  scores: boolean;
  onChanged: () => void;
}) {
  const [viewing, setViewing] = useState<string | null>(null);
  const current = viewing ?? state.next_page ?? "done";
  const { list, reload } = useDocuments();
  const finish = useAction(async () => {
    await api.post("/onboarding/complete");
    onChanged();
  });

  /** A file was saved on the first screen: stay there, with the server's state read again. */
  const changed = () => {
    setViewing("documents");
    void reload();
    onChanged();
  };
  // The server's rule for "this screen is done", not a second one made up here.
  const hasCv = state.pages.find((page) => page.key === "documents")?.complete ?? false;
  const at = Math.max(0, STEPS.findIndex(([key]) => key === current));

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard">
        {/* The name, not a link: nothing is open to them until this is done. */}
        <header className="shell-top">
          <Brand />
          <ThemeToggle />
        </header>

        <div className="step page-in" key={current}>
          <ol className="setup-steps" aria-label="Set up your profile">
            {STEPS.map(([key, label], index) => (
              <li key={key} className={index < at ? "done" : index === at ? "now" : ""}
                  aria-current={index === at ? "step" : undefined}>
                {label}
              </li>
            ))}
          </ol>
          {current === "documents" ? (
            <>
              <h1>Your CV</h1>
              <p className="lede">We read it and fill in your profile. Then you see your jobs.</p>
              <Files kind="cv" list={list} onChanged={changed} />
              {/* Shown, not folded away (owner, 2026-11-17): an option nobody sees is not one. */}
              <section className="optional">
                <h2>Your cover letter</h2>
                <p className="small muted">Optional. Your cover letters are written from it.</p>
                <Files kind="cover_letter" list={list} onChanged={changed} />
              </section>
              <button type="button" className="main" disabled={!hasCv} onClick={() => setViewing("about")}>
                Next
              </button>
            </>
          ) : current === "about" ? (
            <>
              <button type="button" className="back" onClick={() => setViewing("documents")}>
                ← Your CV
              </button>
              <h1>About you</h1>
              <p className="lede">
                {scores ? "We use it to score every job for you and to find more jobs that fit."
                  : "We use it to find more jobs that fit you."}
              </p>
              <ul className="about-asks">
                {ASKS.map((ask) => <li key={ask}>{ask}</li>)}
              </ul>
              <AboutYou label="In your own words" action="Save and continue" main hint="A few sentences is plenty."
                        placeholder={"I want a team leader job in a warehouse near Leeds. I've run shifts of 12 "
                          + "people, I have a forklift licence and I know SAP."}
                        onSaved={() => setViewing("review")} />
              <button type="button" className="linklike skip" onClick={() => setViewing("review")}>
                Skip for now
              </button>
            </>
          ) : current === "review" ? (
            <>
              <button type="button" className="back" onClick={() => setViewing("about")}>
                ← About you
              </button>
              <h1>What we found</h1>
              <p className="lede">Read from what you gave us. Fix anything wrong.</p>
              <WordsForm withIdentity action="Looks good" onSaved={() => {
                setViewing(null);
                onChanged();
              }} />
            </>
          ) : (
            <>
              <button type="button" className="back" onClick={() => setViewing("review")}>
                ← What we found
              </button>
              <h1>You're all set</h1>
              <p className="lede">Your jobs are ready, and new ones come in all day.</p>
              <Error>{finish.error}</Error>
              <button type="button" className="main" disabled={finish.busy} onClick={() => void finish.run()}>
                {finish.busy ? "Opening your jobs…" : "See my jobs"}
              </button>
              <section className="optional">
                <h2>Get alerts</h2>
                {member ? (
                  <>
                    <p className="small muted">
                      Optional. Your alert, My search, already sends new jobs to the bell here. Send them to your
                      phone or Telegram too, from the Alerts page.
                    </p>
                    {/* Their jobs first, then the Alerts page: finishing is what opens the rest of the site. */}
                    <button type="button" className="secondary" disabled={finish.busy} onClick={() => {
                      go("/alerts");
                      void finish.run();
                    }}>Set up alerts</button>
                  </>
                ) : <AlertsForMembers />}
              </section>
            </>
          )}
        </div>
      </main>
    </>
  );
}
