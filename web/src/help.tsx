/** Help and contact, behind the "?" beside the bell on every page (owner, 2026-11-02): the answers
 * people look for first, then a form that reaches a person. */
import { useEffect, useState } from "react";

import { api, type Schema } from "./api";
import { Error, Field, Sheet, useAction } from "./ui";

type Receipt = Schema<"ContactReceipt">;

const ANSWERS: [string, string][] = [
  ["How are my jobs chosen?",
   "A job shows up when one of your green words is in it, and none of your red words is in its " +
   "title or company. It also has to be in a place, language and way of working you picked. You " +
   "can change all of them in My search."],
  ["How do I make a CV or a cover letter?",
   "Link your own AI account in Settings: sign in to Claude, ChatGPT or Google Gemini, or add an API key. " +
   "Upload your CV in My documents, and a cover letter for cover letters. Then tap “Make my CV” or “Make " +
   "my cover letter” under any job. It is written with that account and downloads as a PDF. If something " +
   "is missing, the button tells you what."],
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
      <p className="help-intro">The quick answers first. If yours is not here, write to us and a person replies.</p>

      <h3 className="help-head">Quick answers</h3>
      <section className="answers">
        {ANSWERS.map(([question, answer]) => (
          <details key={question}>
            <summary>{question}</summary>
            <p>{answer}</p>
          </details>
        ))}
      </section>

      {/* Its own card: a person at the other end, said so, and a receipt once it is sent. */}
      <section className="contact">
        {sent ? (
          <div className="contact-sent" role="status">
            <span className="contact-icon ok" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M6 12.5l4 4 8-8.5" /></svg>
            </span>
            <h3>Message received</h3>
            <p>We will reply to {sent.email}.</p>
            <p className="contact-ref">Your reference <code>{sent.reference}</code></p>
            <button type="button" className="secondary" onClick={() => setSent(null)}>Write another</button>
          </div>
        ) : (
          <form onSubmit={(event) => {
            event.preventDefault();
            if (ready) void send.run();
          }}>
            <div className="contact-head">
              <span className="contact-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><path d="M4 6.5h16v11H4zM4.5 7l7.5 6 7.5-6" /></svg>
              </span>
              <div>
                <h3>Write to us</h3>
                <p>A person reads every message and replies by email.</p>
              </div>
            </div>
            <Field label="Subject" hint="A few words about what it is.">
              <input value={subject} maxLength={120} onChange={(event) => setSubject(event.target.value)} />
            </Field>
            <Field label="Message"
                   hint="What you were trying to do, and what happened. Please leave out passwords and keys.">
              <textarea rows={6} value={message} maxLength={4000}
                        onChange={(event) => setMessage(event.target.value)} />
            </Field>
            <Error>{send.error}</Error>
            <button type="submit" className="main contact-send" disabled={!ready || send.busy}>
              {send.busy ? "Sending…" : "Send message"}
            </button>
          </form>
        )}
      </section>
      {address ? (
        <p className="contact-mail">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6.5h16v11H4zM4.5 7l7.5 6 7.5-6" /></svg>
          <span>Prefer your own email app? Write to <a href={`mailto:${address}`}>{address}</a>.</span>
        </p>
      ) : null}
    </>
  );
}
