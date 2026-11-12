/** Three screens: your CV, what we found in it, done. Which one is the server's answer (`/dashboard`),
 * held back only so a person stays on a screen until they press its button. */
import { useState } from "react";

import { Alerts, AlertsForMembers } from "../alerts";
import { api, type Schema } from "../api";
import { ThemeToggle } from "../theme";
import { Brand } from "../wheel";
import { Error, useAction } from "../ui";
import { AboutYou, Files, useDocuments } from "./DocumentsPage";
import { WordsForm } from "./WordsPage";

/** The three steps, as the bar over each screen names them. */
const STEPS = [["documents", "Your CV"], ["review", "Your search"], ["done", "Done"]] as const;

export function Onboarding({ state, member, onChanged }: {
  state: Schema<"OnboardingSteps">;
  /** Has a membership: alerts are a member's (`AlertsForMembers`). */
  member: boolean;
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
              <details className="fold-plain">
                <summary>Add more (optional)</summary>
                <Files kind="cover_letter" list={list} onChanged={changed} />
                <AboutYou onSaved={changed} />
              </details>
              <button type="button" className="main" disabled={!hasCv} onClick={() => setViewing("review")}>
                Next
              </button>
            </>
          ) : current === "review" ? (
            <>
              <button type="button" className="back" onClick={() => setViewing("documents")}>
                ← Your CV
              </button>
              <h1>What we found</h1>
              <p className="lede">Read from your CV. Fix anything wrong.</p>
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
                <p className="small muted">Optional. New jobs by Telegram, in this browser, or by email.</p>
                {member ? <Alerts /> : <AlertsForMembers />}
              </section>
            </>
          )}
        </div>
      </main>
    </>
  );
}
