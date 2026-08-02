/**
 * The seven pages, one question at a time.
 *
 * **Which page you are on is the server's answer, not this component's.** `next_page` is derived
 * from the rows each page writes, so deleting a credential later correctly reopens that page — and
 * there is no local step counter to disagree with it. It also means this file cannot let anybody
 * skip: a page is finished when its rows exist, and no button here can fake that.
 *
 * **No cards.** A form that is already one question per page does not need a box drawn around it to
 * say where it starts; the whitespace does that, and a container competes with the question for
 * attention. Title, at most one line of context, the field, and the way forward.
 *
 * **The way forward is always on the page, and pressing it is the user's job.** It used to be that
 * saving a field moved you on by itself, and nothing else could: a page you had already finished
 * had no control at all, so revisiting one from the rail was a dead end, and a page that saves more
 * than once -- Telegram takes a token, then reads the chat id -- could vanish part-way through.
 *
 * So Continue is always present and disabled until the page is satisfied, which does mean a control
 * you can see before you can use it. That is the lesser problem: a greyed button with "finish this
 * step to continue" beside it explains itself, where an absent one leaves somebody hunting for the
 * thing that is not there.
 */
import { useState } from "react";

import { api, type OnboardingState } from "../api";
import { ThemeToggle } from "../theme";
import { Error, useAction } from "../ui";
import { ConfigStep } from "./onboarding/ConfigStep";
import { ClaudeStep, ProxyStep, TelegramStep } from "./onboarding/Credentials";
import { DocumentsStep, HistoryStep } from "./onboarding/DocumentsStep";
import { IdentityStep } from "./onboarding/IdentityStep";

/** Titles a non-technical person can act on. Most pages need no second line at all. */
const TITLES: Record<string, { title: string; lede?: string }> = {
  identity: { title: "What should employers call you?" },
  documents: { title: "Share your CV" },
  history: { title: "Tell us what you have done" },
  telegram: {
    title: "Where should we send jobs?",
    lede: "Matches arrive on Telegram, so you get them wherever you are.",
  },
  claude: {
    title: "Connect your Claude account",
    lede: "Everything runs on your own key, so you can see exactly what it costs.",
  },
  proxy: {
    title: "Add a proxy",
    lede: "LinkedIn is fetched through your own connection and never a shared one.",
  },
  config: { title: "What are you looking for?" },
};

export function Onboarding({
  state,
  onChanged,
}: {
  state: OnboardingState;
  onChanged: () => void;
}) {
  // The server's answer, overridden only so somebody can go *back* to something they finished. It
  // never lets anybody move forward past an unfinished page.
  const [viewing, setViewing] = useState<string | null>(null);
  const current = viewing ?? state.next_page ?? "done";
  const index = state.pages.findIndex((page) => page.key === current);
  const copy = TITLES[current];

  const finish = useAction(async () => {
    await api.post("/onboarding/complete");
    onChanged();
  });

  /**
   * Something on this page saved. **Refresh what is complete, and stay where we are.**
   *
   * It used to clear `viewing`, which handed the choice back to `next_page` and threw the user onto
   * the following step the instant a field validated. Mid-sentence, sometimes: the Telegram page
   * saves a token and then wants to read the chat id, and the page had already gone. Moving on is
   * now something a person does, not something that happens to them.
   */
  const done = (): void => {
    setViewing(current);
    onChanged();
  };

  /** The page before this one, if it is finished — so back never lands somewhere unusable. */
  const previous = index > 0 ? state.pages[index - 1] : undefined;
  const next = index >= 0 ? state.pages[index + 1] : undefined;
  const ready = index >= 0 && state.pages[index]?.complete === true;

  // Clearing `viewing` hands control back to `next_page`, which is what shows the finishing screen
  // once the last page is done.
  const advance = (): void => setViewing(next?.key ?? null);

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard">
        <ThemeToggle floating />
        <nav style={{ border: "none", marginBottom: 0 }}>
          <span className="brand">Mahoraga</span>
        </nav>

        {/* Numbered circles, joined by a rule. A finished one is filled and carries a check; the
            current one is ringed; a future one is a dim number. Finished ones are clickable —
            going back to correct something is exactly what saving per page buys. */}
        <ol className="dots" aria-label={index >= 0 ? `Step ${index + 1} of ${state.pages.length}` : "Finished"}>
          {state.pages.map((page, position) => (
            <li key={page.key} className="dot-wrap">
              {position > 0 ? <span className="dot-rule" aria-hidden="true" /> : null}
              <button
                type="button"
                className={`dot${page.key === current ? " on" : ""}${page.complete ? " done" : ""}`}
                title={page.title}
                aria-current={page.key === current ? "step" : undefined}
                disabled={!page.complete && page.key !== current}
                onClick={() => page.complete && setViewing(page.key)}
              >
                {page.complete && page.key !== current ? <Check /> : position + 1}
              </button>
              <span className={`dot-label${page.key === current ? " on" : ""}`}>{page.title}</span>
            </li>
          ))}
        </ol>

        <div className="step page-in" key={current}>
          {copy ? (
            <>
              {previous ? (
                <button type="button" className="back" onClick={() => setViewing(previous.key)}>
                  <Arrow /> {previous.title}
                </button>
              ) : null}
              <p className="kicker">
                Step {index + 1} of {state.pages.length}
              </p>
              <h1>{copy.title}</h1>
              {copy.lede ? <p className="lede">{copy.lede}</p> : null}
              <Step which={current} onDone={done} />

              {/* **Always here, enabled once the page is satisfied.** Previously the only way
                  forward was for a save to fire the jump itself, so a page that was already
                  complete -- revisited from the rail, or finished a moment ago -- was a dead end
                  with nothing to press. */}
              <div className="wizard-nav">
                <button
                  type="button"
                  className={ready ? "advance ready" : "advance"}
                  disabled={!ready}
                  onClick={advance}
                >
                  {next ? `Continue to ${next.title}` : "Continue"}
                  <Arrow forward />
                </button>
                {!ready ? (
                  <span className="small muted">Finish this step to continue.</span>
                ) : null}
              </div>
            </>
          ) : (
            <>
              <p className="kicker">All set</p>
              <h1>That is everything</h1>
              <p className="lede">
                Next you will build your profile in a conversation. That is the first thing that
                uses your Claude credential, and you decide when.
              </p>
              <Error>{finish.error}</Error>
              <button className="advance ready" disabled={finish.busy} onClick={() => void finish.run()}>
                {finish.busy ? "Finishing…" : "Go to my dashboard"}
              </button>
            </>
          )}
        </div>
      </main>
    </>
  );
}

/** One glyph, mirrored for the forward case, so back and continue read as a pair. */
function Arrow({ forward }: { forward?: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      style={forward ? { transform: "rotate(180deg)" } : undefined}
    >
      <path
        d="M15 5l-7 7 7 7"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Check() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4.5 12.5l5 5 10-11" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Step({ which, onDone }: { which: string; onDone: () => void }) {
  switch (which) {
    case "identity":
      return <IdentityStep onDone={onDone} />;
    case "documents":
      return <DocumentsStep onDone={onDone} />;
    case "history":
      return <HistoryStep onDone={onDone} />;
    case "telegram":
      return <TelegramStep onDone={onDone} />;
    case "claude":
      return <ClaudeStep onDone={onDone} />;
    case "proxy":
      return <ProxyStep onDone={onDone} />;
    case "config":
      return <ConfigStep onDone={onDone} />;
    default:
      return null;
  }
}
