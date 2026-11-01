/** Help and contact, at the end of Settings: the answers people look for first, then a form that
 * reaches a person. */
import { useEffect, useState } from "react";

import { api, type Schema } from "./api";
import { Error, Field, useAction } from "./ui";

type Receipt = Schema<"ContactReceipt">;

const ANSWERS: [string, string][] = [
  ["How are my jobs chosen?",
   "A job shows up when one of your green words is in it, and none of your red words is in its " +
   "title or company. It also has to be in a place, language and way of working you picked. You " +
   "can change all of them in My search."],
  ["How do I make a CV or a cover letter?",
   "Tap “Make my CV” or “Make my cover letter” under any job. It is made from the CV in My documents " +
   "in a few seconds, and downloads as a PDF."],
  ["How do I get new jobs on my phone?",
   "In Settings, turn on notifications in this browser, or connect Telegram. Both are optional, and " +
   "your Jobs page fills up either way."],
  ["Where are the jobs I applied to?",
   "Tap “I applied” on a job, and it is kept in Applied, even after it stops matching your search."],
  ["I forgot my password.",
   "Sign out, then tap “Forgot your password?” on the sign-in page. We email you a link to set a new one."],
];

export function HelpAndContact() {
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
