/** The seven pages, one question at a time. */
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
  // The server's answer, overridden only so somebody can go *back* to something they finished.
  const [viewing, setViewing] = useState<string | null>(null);
  const current = viewing ?? state.next_page ?? "done";
  const index = state.pages.findIndex((page) => page.key === current);
  const copy = TITLES[current];

  const finish = useAction(async () => {
    await api.post("/onboarding/complete");
    onChanged();
  });

  /** Something on this page saved. */
  const done = (): void => {
    setViewing(current);
    onChanged();
  };

  /** The page before this one, if it is finished — so back never lands somewhere unusable. */
  const previous = index > 0 ? state.pages[index - 1] : undefined;
  const next = index >= 0 ? state.pages[index + 1] : undefined;
  const ready = index >= 0 && state.pages[index]?.complete === true;

  // Clearing `viewing` hands control back to `next_page`.
  const advance = (): void => setViewing(next?.key ?? null);

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard">
        <ThemeToggle floating />
        <nav style={{ border: "none", marginBottom: 0 }}>
          <span className="brand">Mahoraga</span>
        </nav>

        {/* Numbered circles, joined by a rule. */}
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

              {/* Always here, enabled once the page is satisfied. */}
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
