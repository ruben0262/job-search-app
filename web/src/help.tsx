/** Help and contact, behind the "?" beside the bell on every page (owner, 2026-11-02): the answers
 * people look for first, then a form that reaches a person. */
import { useEffect, useState } from "react";

import { api, type Schema } from "./api";
import { PAUSED } from "./documents";
import { Error, Field, Sheet, useAction } from "./ui";

type Receipt = Schema<"ContactReceipt">;

const ANSWERS: [string, string][] = [
  ["How are my jobs chosen?",
   "A job shows up when one of your green words is in it, and none of your red words is in its " +
   "title or company. It also has to be in a place, language and way of working you picked. You " +
   "can change all of them in My search."],
  ["How do I make a CV or a cover letter?", `${PAUSED} The ones you made before are still under their ` +
   "jobs and in My documents, to download again."],
  // Nobody applies through the site (owner, 2026-11-06): it says so where people look first.
  ["Can I apply to jobs here?",
   "No, you apply on the job's own site, and we make that quick. Tap “Apply on …” " +
   "under the job: it opens on the site it was posted on, and you apply there. Then tap “Mark as applied”."],
  ["How do I get new jobs on my phone?",
   "In Settings, turn on notifications in this browser, or connect Telegram. Both are optional, and " +
   "your Jobs page fills up either way."],
  ["How do I stop seeing a job?",
   "Tap “Skip”, then “Skip it”. It leaves your Jobs page and does not come back, even after a refresh."],
  ["Where are the jobs I applied to?",
   "Tap “Mark as applied” on a job. It leaves your Jobs page and is kept in Applied, even after it stops " +
   "matching your search."],
  ["I forgot my password.",
   "Sign out, then tap “Forgot your password?” on the sign-in page. We email you a link to set a new one."],
];

/** The "?" beside the bell, and what it opens over the page. */
export function HelpButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="bell help" aria-label="Help and contact" title="Help and contact"
              onClick={() => setOpen(true)}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M7.8 8.6a4.2 4.2 0 1 1 6.1 3.8c-1.1.5-1.9 1.5-1.9 2.7v.7M12 20h.01" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open ? <Sheet title="Help and contact" onClose={() => setOpen(false)}><HelpAndContact /></Sheet> : null}
    </>
  );
}

function HelpAndContact() {
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [sent, setSent] = useState<Receipt | null>(null);
  const [address, setAddress] = useState<string | null>(null);
  useEffect(() => {
    api.get<Schema<"ContactAddress">>("/contact").then((body) => setAddress(body.email)).catch(() => undefined);
  }, []);
  const send = useAction(async () => {
    setSent(await api.post<Receipt>("/contact", { subject: subject.trim(), message: message.trim() }));
    setSubject("");
    setMessage("");
  });
  const ready = subject.trim().length >= 3 && message.trim().length >= 10;

  return (
    <>
      <p className="small muted">The quick answers first. If yours is not here, write to us and a person replies.</p>

      <section className="answers">
        {ANSWERS.map(([question, answer]) => (
          <details key={question} className="fold-plain">
            <summary>{question}</summary>
            <p>{answer}</p>
          </details>
        ))}
      </section>

      <section className="contact">
        {sent ? (
          <div role="status">
            <h3>Message received</h3>
            <p>
              Your reference is <strong>{sent.reference}</strong>. We will reply to {sent.email}.
            </p>
            <button type="button" className="secondary" onClick={() => setSent(null)}>Write another</button>
          </div>
        ) : (
          <form onSubmit={(event) => {
            event.preventDefault();
            if (ready) void send.run();
          }}>
            <h3>Write to us</h3>
            <Field label="Subject" hint="A few words about what it is.">
              <input value={subject} maxLength={120} onChange={(event) => setSubject(event.target.value)} />
            </Field>
            <Field label="Message"
                   hint="What you were trying to do, and what happened. Please leave out passwords and keys.">
              <textarea rows={6} value={message} maxLength={4000}
                        onChange={(event) => setMessage(event.target.value)} />
            </Field>
            <Error>{send.error}</Error>
            <button type="submit" className="main" disabled={!ready || send.busy}>
              {send.busy ? "Sending…" : "Send message"}
            </button>
          </form>
        )}
        {address ? (
          <p className="small muted">
            Prefer your own email app? Write to <a href={`mailto:${address}`}>{address}</a>.
          </p>
        ) : null}
      </section>
    </>
  );
}
