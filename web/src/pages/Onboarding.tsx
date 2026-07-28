/**
 * The seven pages, driven entirely by `GET /onboarding/state`.
 *
 * **Which page you are on is the server's answer, not this component's.** `next_page` is derived
 * from the rows each page writes, so deleting a credential later correctly reopens that page —
 * and there is no local step counter to disagree with it. That also means this file cannot let
 * somebody skip: a page is finished when its rows exist, and no button here can fake that.
 *
 * Every page is required. A half-configured account produces a bad feed, and the user concludes
 * the product does not work rather than that they missed a step.
 */
import { useState } from "react";

import { api, type OnboardingState } from "../api";
import { Card, Error, useAction } from "../ui";
import { ClaudeStep, ProxyStep, TelegramStep } from "./onboarding/Credentials";
import { ConfigStep } from "./onboarding/ConfigStep";
import { DocumentsStep, HistoryStep } from "./onboarding/DocumentsStep";
import { IdentityStep } from "./onboarding/IdentityStep";

export function Onboarding({
  state,
  onChanged,
}: {
  state: OnboardingState;
  onChanged: () => void;
}) {
  // The server's answer, with a local override only so somebody can go *back* and change something
  // they already did. It never lets anybody go forward past an unfinished page.
  const [viewing, setViewing] = useState<string | null>(null);
  const current = viewing ?? state.next_page ?? "done";
  const page = state.pages.find((candidate) => candidate.key === current);

  const finish = useAction(async () => {
    await api.post("/onboarding/complete");
    onChanged();
  });

  const done = (): void => {
    setViewing(null);
    onChanged();
  };

  return (
    <main className="wizard">
      <h1>Set up your account</h1>
      <p className="muted">
        Seven short steps. Nothing here costs anything or calls a model — that comes later, when you
        choose it.
      </p>

      <ol className="steps">
        {state.pages.map((candidate) => (
          <li
            key={candidate.key}
            className={candidate.key === current ? "on" : candidate.complete ? "done" : ""}
            onClick={() => candidate.complete && setViewing(candidate.key)}
          >
            {candidate.title}
          </li>
        ))}
      </ol>

      {page ? (
        <Card title={page.title}>
          <p className="muted small">{page.detail}</p>
          <Step which={page.key} onDone={done} />
        </Card>
      ) : (
        <Card title="That is everything">
          <p>
            Your account is configured. Next you will process your documents to build your profile —
            that is the first thing that uses your Claude credential, and you decide when.
          </p>
          <Error>{finish.error}</Error>
          <button disabled={finish.busy} onClick={() => void finish.run()}>
            {finish.busy ? "Finishing…" : "Go to my dashboard"}
          </button>
        </Card>
      )}

      {viewing && state.next_page ? (
        <p className="small">
          <a href="#/" onClick={() => setViewing(null)}>
            Back to where I was
          </a>
        </p>
      ) : null}
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
