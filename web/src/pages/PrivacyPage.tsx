/** Who sees your data — and, signed in, the one place to agree to AI-written documents, which are
 * the only thing that sends your text anywhere. */
import { useEffect, useState } from "react";

import { api, type Schema } from "../api";
import { ThemeToggle } from "../theme";
import { Card, Error, useAction } from "../ui";
import { Brand } from "../wheel";

export function PrivacyPage() {
  const [disclosure, setDisclosure] = useState<Schema<"Disclosure"> | null>(null);
  const [consent, setConsent] = useState<Schema<"ConsentState"> | null>(null);

  // Signed in, the consent carries the policy; signed out, the policy alone is public.
  const load = () =>
    api.get<Schema<"ConsentState">>("/account/consent").then((body) => {
      setConsent(body);
      setDisclosure(body.disclosure);
    }).catch(() => api.get<Schema<"Disclosure">>("/legal/privacy").then(setDisclosure).catch(() => undefined));
  useEffect(() => {
    void load();
  }, []);
  const agree = useAction(async () => {
    await api.post("/account/consent", { version: disclosure?.version });
    await load();
  });

  if (!disclosure) return <main className="centered">Loading…</main>;

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard page-in">
        <header className="shell-top">
          <Brand href="#/" />
          <ThemeToggle />
        </header>
        <h1 style={{ marginTop: "2.5rem" }}>Who sees your data</h1>
        {/* What they agree to is this version of it. */}
        <p className="small muted">Version {disclosure.version}</p>

        <Card title={disclosure.headline}>
          {disclosure.body.split("\n\n").map((paragraph, index) => (
            <p key={index}>{paragraph.replaceAll("**", "")}</p>
          ))}
          {consent && !consent.granted ? (
            <>
              <Error>{agree.error}</Error>
              <button type="button" disabled={agree.busy} onClick={() => void agree.run()}>
                {agree.busy ? "Saving…" : "I agree"}
              </button>
            </>
          ) : consent?.granted ? (
            <p className="saved-flash">You agreed. AI-written documents are open to you.</p>
          ) : null}
        </Card>

        {disclosure.processors.map((processor) => (
          <Card key={processor.name} title={processor.name}>
            <p>{processor.purpose}</p>
            <p className="small muted">
              <strong>What they receive:</strong> {processor.data}
            </p>
            <p className="small muted">
              <strong>Where:</strong> {processor.location}
              {processor.on_your_credential ? " · paid for by your own credential, not ours" : ""}
            </p>
          </Card>
        ))}

        <Card title="Cookies, and what your browser keeps">
          <p className="small muted">
            Each one is needed for the site to work. Nothing measures you or shows you adverts.
          </p>
          {disclosure.cookies.map((cookie) => (
            <p key={cookie.name} className="small">
              <strong>{cookie.name}</strong>: {cookie.purpose} <span className="muted">Kept: {cookie.kept}.</span>
            </p>
          ))}
        </Card>

        <p>
          <a href="#/">Back</a>
        </p>
      </main>
    </>
  );
}
