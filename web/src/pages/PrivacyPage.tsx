/**
 * The privacy page. Public, because signup links it before anybody has an account.
 *
 * Rendered from `GET /legal/privacy` rather than written here as prose. The API serves it from
 * `libs/legal`, which is the same source the consent screen uses — so a processor added to the
 * platform cannot be missing from the page a user read before agreeing.
 */
import { useEffect, useState } from "react";

import { api, type Consent } from "../api";
import { Card } from "../ui";
import { ThemeToggle } from "../theme";

export function PrivacyPage() {
  const [disclosure, setDisclosure] = useState<Consent["disclosure"] | null>(null);

  useEffect(() => {
    void api
      .get<Consent["disclosure"]>("/legal/privacy")
      .then(setDisclosure)
      .catch(() => undefined);
  }, []);

  if (!disclosure) return <main className="centered">Loading…</main>;

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard page-in">
        <ThemeToggle floating />
        <nav style={{ border: "none", marginBottom: 0 }}>
          <a className="brand" href="#/">
            Mahoraga
          </a>
        </nav>
        <p className="kicker" style={{ marginTop: "2.5rem" }}>
          Privacy · version {disclosure.version}
        </p>
        <h1>Who sees your data</h1>

      <Card title={disclosure.headline}>
        {disclosure.body.split("\n\n").map((paragraph, index) => (
          <p key={index}>{paragraph.replaceAll("**", "")}</p>
        ))}
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

        <p>
          <a href="#/">Back</a>
        </p>
      </main>
    </>
  );
}
