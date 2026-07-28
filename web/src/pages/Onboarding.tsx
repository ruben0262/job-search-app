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
 * **The way forward appears rather than sitting there greyed out.** A control that is present and
 * refuses to work invites clicking it to find out why; one that fades in when the page is satisfied
 * answers "what now" without anybody having to ask.
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

  const done = (): void => {
    setViewing(null);
    onChanged();
  };

  /** The page before this one, if it is finished — so back never lands somewhere unusable. */
  const previous = index > 0 ? state.pages[index - 1] : undefined;

  return (
    <main className="wizard">
      <ThemeToggle floating />

      {previous ? (
        <a className="back" href="#/" onClick={() => setViewing(previous.key)}>
          ‹ Back
        </a>
      ) : (
        <span className="back" style={{ visibility: "hidden" }}>
          ‹ Back
        </span>
      )}

      <ol className="steps" aria-label={`Step ${index + 1} of ${state.pages.length}`}>
        {state.pages.map((page) => (
          <li
            key={page.key}
            className={page.key === current ? "on" : page.complete ? "done" : ""}
            title={page.title}
            onClick={() => page.complete && setViewing(page.key)}
          />
        ))}
      </ol>

      <div className="step">
        {copy ? (
          <>
            <h1>{copy.title}</h1>
            {copy.lede ? <p className="lede">{copy.lede}</p> : null}
            <Step which={current} onDone={done} />
          </>
        ) : (
          <>
            <h1>That is everything</h1>
            <p className="lede">
              Next you will process your documents to build your profile. That is the first thing
              that uses your Claude credential, and you decide when.
            </p>
            <Error>{finish.error}</Error>
            <button className="advance ready" disabled={finish.busy} onClick={() => void finish.run()}>
              {finish.busy ? "Finishing…" : "Go to my dashboard"}
            </button>
          </>
        )}
      </div>
    </main>
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
