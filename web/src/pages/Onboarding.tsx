/** Three screens: your CV, what we found in it, done. Which one is the server's answer (`/dashboard`),
 * held back only so a person stays on a screen until they press its button. */
import { useState } from "react";

import { Alerts } from "../alerts";
import { api, type Schema } from "../api";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { AboutYou, Files, useDocuments } from "./DocumentsPage";
import { WordsForm } from "./WordsPage";

export function Onboarding({ state, onChanged }: {
  state: Schema<"OnboardingSteps">;
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

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard">
        <ThemeToggle floating />
        <nav style={{ border: "none", marginBottom: 0 }}>
          <span className="brand">Mahoraga</span>
        </nav>

        <div className="step page-in" key={current}>
          <p className="kicker">Step {current === "documents" ? 1 : current === "review" ? 2 : 3} of 3</p>
          {current === "documents" ? (
            <>
              <h1>Your CV</h1>
              <p className="lede">We read it and fill in the next screen for you.</p>
              <Files kind="cv" list={list} onChanged={changed} />
              <details className="fold-plain">
                <summary>Add more (optional)</summary>
                <p className="small muted">A cover letter you wrote, so yours sound like you.</p>
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
              <p className="lede">We filled this in from your CV. Change anything that is wrong.</p>
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
                <h2>Get alerts on your phone</h2>
                <p className="small muted">
                  Optional. Turn one on, and from now on, new jobs that match your words come to your
                  phone.
                </p>
                <Alerts />
              </section>
            </>
          )}
        </div>
      </main>
    </>
  );
}
