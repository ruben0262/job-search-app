/**
 * Page 1. The name is not the account email — it is what a recruiter reads at the top of a
 * generated CV, so it is asked for rather than derived from `r.lekli@…`.
 *
 * The platform of a link is inferred server-side from its host, so this form does not ask. Making
 * somebody classify their own GitHub URL is a question with one possible answer.
 */
import { useEffect, useState } from "react";

import { api } from "../../api";
import { Error, Field, useAction } from "../../ui";

type Link = { url: string; platform?: string };

export function IdentityStep({ onDone }: { onDone: () => void }) {
  const [fullName, setFullName] = useState("");
  const [links, setLinks] = useState<Link[]>([{ url: "" }]);

  useEffect(() => {
    void api
      .get<{ full_name: string | null; links: Link[] }>("/onboarding/identity")
      .then((stored) => {
        setFullName(stored.full_name ?? "");
        if (stored.links.length) setLinks(stored.links);
      })
      .catch(() => undefined);
  }, []);

  const save = useAction(async () => {
    await api.put("/onboarding/identity", {
      full_name: fullName,
      links: links.filter((link) => link.url.trim()).map((link) => ({ url: link.url.trim() })),
    });
    onDone();
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <Field label="Full name" hint="Exactly as it should appear on a CV you send to an employer.">
        <input value={fullName} required onChange={(event) => setFullName(event.target.value)} />
      </Field>

      <Field label="Links" hint="LinkedIn, GitHub, a portfolio. Optional, and paste them however you like.">
        <div>
          {links.map((link, index) => (
            <input
              key={index}
              value={link.url}
              placeholder="github.com/you"
              style={{ marginBottom: "0.4rem" }}
              onChange={(event) => {
                const next = [...links];
                next[index] = { url: event.target.value };
                setLinks(next);
              }}
            />
          ))}
        </div>
      </Field>

      <div className="row">
        <button type="button" className="secondary" onClick={() => setLinks([...links, { url: "" }])}>
          Add another link
        </button>
      </div>

      <Error>{save.error}</Error>
      <p style={{ marginTop: "1rem" }}>
        <button type="submit" disabled={save.busy}>
          {save.busy ? "Saving…" : "Save and continue"}
        </button>
      </p>
    </form>
  );
}
