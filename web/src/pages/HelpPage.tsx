/** Help: ask the help chat, watch the answer arrive, and tap to make a change it offers. */
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { api } from "../api";
import type { components } from "../api-types";
import { Banner, Error, useAction } from "../ui";

type Page = components["schemas"]["SupportPage"];
type Message = components["schemas"]["SupportMessage"];
type Step = components["schemas"]["SupportStep"];
type Event = components["schemas"]["SupportEvent"];
type Tap = { run: (messageId: number, index: number) => Promise<void>; busy: boolean };

const EXAMPLES = ["Why is a job on my list?", "Add a word to my search", "How do I get job alerts on my phone?"];

const tall: CSSProperties = { minHeight: 44 };
const said: CSSProperties = { whiteSpace: "pre-wrap", overflowWrap: "anywhere", lineHeight: 1.55, margin: 0 };
const bubble: CSSProperties = {
  ...said,
  alignSelf: "flex-end",
  maxWidth: "85%",
  padding: "0.6rem 0.9rem",
  borderRadius: "var(--radius-lg)",
  border: "1px solid var(--hairline)",
  background: "var(--bg-raised)",
};

export function HelpPage() {
  const [page, setPage] = useState<Page | null>(null);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => setPage(await api.get<Page>("/support")), []);
  useEffect(() => {
    load().catch(() => setFailed(true));
  }, [load]);

  // The answer being written, followed as it arrives: the page never asks again while it waits.
  const writing = page?.messages.find((m) => m.status === "pending" || m.status === "streaming");
  const writingId = writing?.id;
  useEffect(() => {
    if (writingId === undefined) return;
    const source = new EventSource(`/support/replies/${writingId}/stream`);
    const update = (change: (m: Message) => Message) =>
      setPage((p) => p && { ...p, messages: p.messages.map((m) => (m.id === writingId ? change(m) : m)) });
    const on = (name: string, handle: (data: Event) => void) =>
      source.addEventListener(name, (event) => handle(JSON.parse((event as MessageEvent).data) as Event));
    on("snapshot", (d) => update((m) => ({ ...m, text: d.text ?? "", steps: d.steps ?? [] })));
    on("delta", (d) => update((m) => ({ ...m, text: m.text + (d.text ?? ""), status: "streaming" })));
    on("step", (d) =>
      update((m) => {
        const steps = [...m.steps];
        if (d.step) steps[d.index ?? steps.length] = d.step;
        return { ...m, steps };
      }),
    );
    on("done", () => {
      source.close();
      void load();
    });
    // Refused rather than dropped (a dropped stream reconnects by itself): read the page again.
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) void load();
    };
    return () => source.close();
  }, [writingId, load]);

  const lastText = page?.messages[page.messages.length - 1]?.text;
  useEffect(() => {
    // Follows the answer down the page, unless the reader has scrolled up to read something else.
    if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 160)
      bottom.current?.scrollIntoView({ block: "end" });
  }, [page?.messages.length, lastText]);

  const ask = useAction(async (question: string) => {
    await api.post("/support/messages", { text: question });
    setDraft("");
    await load();
  });

  const tap = useAction(async (messageId: number, index: number) => {
    const step = await api.post<Step>(`/support/replies/${messageId}/steps/${index}`);
    setPage((p) => p && {
      ...p,
      messages: p.messages.map((m) =>
        m.id === messageId ? { ...m, steps: m.steps.map((s, i) => (i === index ? step : s)) } : m),
    });
  });

  if (failed) return <p className="error">This page did not load. Refresh to try again.</p>;
  if (!page) return <p className="muted">Loading…</p>;

  const busy = ask.busy || writing !== undefined;
  const send = () => {
    if (draft.trim() && !busy) void ask.run(draft.trim());
  };

  return (
    <div style={{ maxWidth: "44rem" }}>
      <p className="kicker">Help</p>
      <h1>Ask us anything</h1>
      <p className="muted">
        How the site works, why a job is on your list, or which words to use. Answers are written by AI and can be
        wrong.
      </p>
      <p className="small">
        {page.ai.payer === "own"
          ? `Unlimited answers, on your own ${page.ai.provider} key.`
          : `AI requests left today: ${page.ai.left ?? 0} of ${page.ai.per_day}`}
      </p>

      {page.closed ? (
        <>
          <Banner level="info">{page.closed}</Banner>
          <HowItWorks />
        </>
      ) : null}

      <div aria-live="polite" style={{ display: "flex", flexDirection: "column", gap: "1rem", margin: "1.5rem 0" }}>
        {page.messages.map((message) =>
          message.role === "user" ? (
            <p key={message.id} style={bubble}>
              {message.text}
            </p>
          ) : (
            <Reply key={message.id} message={message} tap={tap} />
          ),
        )}
        <div ref={bottom} />
      </div>

      {page.closed ? null : (
        <>
          {page.messages.length === 0 ? (
            <div className="row" style={{ flexWrap: "wrap", gap: "0.5rem", marginBottom: "1rem" }}>
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  className="chip"
                  style={tall}
                  disabled={busy}
                  onClick={() => void ask.run(example)}
                >
                  {example}
                </button>
              ))}
            </div>
          ) : null}
          <form
            style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "flex-end" }}
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <textarea
              rows={2}
              value={draft}
              maxLength={2000}
              aria-label="Your question"
              placeholder={writing ? "Writing your answer…" : "Type your question"}
              disabled={busy}
              style={{ flex: "1 1 16rem", minHeight: 44 }}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter asks, Shift+Enter starts a new line.
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
            />
            <button type="submit" style={tall} disabled={busy || !draft.trim()}>
              {ask.busy ? "Sending…" : "Ask"}
            </button>
          </form>
          <Error>{ask.error ?? tap.error}</Error>
          <p className="small muted" style={{ marginTop: "0.75rem" }}>
            {page.consent
              ? "It can read the words we found in your documents."
              : "It cannot see your documents, only your words and how the site works."}{" "}
            It never changes anything without your tap.
          </p>
        </>
      )}
    </div>
  );
}

/** One answer: its text as it arrives, what it looked at, and a button for each change it offers. */
function Reply({ message, tap }: { message: Message; tap: Tap }) {
  const waiting = message.status === "pending" || message.status === "streaming";
  return (
    <div>
      {message.text ? (
        <p style={said}>
          {message.text}
          {waiting ? <span className="muted"> ▍</span> : null}
        </p>
      ) : waiting ? (
        <p className="muted">Writing your answer…</p>
      ) : null}
      {message.error ? <p className="error">{message.error}</p> : null}
      {message.steps.map((step, index) =>
        step.state ? (
          step.state === "proposed" ? (
            <button
              key={index}
              type="button"
              className="secondary"
              style={{ ...tall, display: "block", marginTop: "0.5rem", textAlign: "left" }}
              disabled={tap.busy || waiting}
              onClick={() => void tap.run(message.id, index)}
            >
              {step.label}
            </button>
          ) : (
            <p key={index} className="small" style={{ marginTop: "0.5rem" }}>
              {step.state === "done" ? "Done: " : "Not done: "}
              {step.label}. {step.output}
            </p>
          )
        ) : (
          <details key={index} className="small muted" style={{ marginTop: "0.35rem" }}>
            <summary style={{ cursor: "pointer" }}>
              {step.error ? "× " : "· "}
              {step.label}
            </summary>
            {step.output ? <p style={{ ...said, margin: "0.35rem 0 0 1rem" }}>{step.output}</p> : null}
          </details>
        ),
      )}
    </div>
  );
}

/** What anyone can read, whether or not the chat can answer today. */
function HowItWorks() {
  return (
    <ol style={{ lineHeight: 1.6, paddingLeft: "1.25rem" }}>
      <li>
        Your words find your jobs: green words show jobs, red words hide them. Change them in{" "}
        <a href="#/words">My words</a>.
      </li>
      <li>On any job, tap Make my CV or Make my cover letter, then download it.</li>
      <li>
        Alerts on your phone are optional. Turn them on in <a href="#/settings">Settings</a>, where you can also add
        your own AI key.
      </li>
    </ol>
  );
}
