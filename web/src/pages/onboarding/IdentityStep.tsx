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
      full_name: fullName.trim(),
      links: links.filter((link) => link.url.trim()).map((link) => ({ url: link.url.trim() })),
    });
    onDone();
  });

  const ready = fullName.trim().length >= 2;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) void save.run();
      }}
    >
      <Field label="Your name" hint="As it should appear on a CV you send to an employer.">
        <input
          value={fullName}
          autoFocus
          autoComplete="name"
          onChange={(event) => setFullName(event.target.value)}
        />
      </Field>

      <div className="field">
        {/* The add button sits above the first row, on the right, where it reads as "more of these"
            rather than as an action on the row beneath it. */}
        <div className="repeat-head">
          <span className="label" style={{ marginBottom: 0 }}>
            Links
          </span>
          <button
            type="button"
            className="icon-btn"
            aria-label="Add another link"
            title="Add another link"
            onClick={() => setLinks([...links, { url: "" }])}
          >
            +
          </button>
        </div>
        <span className="hint">LinkedIn, GitHub, a portfolio. Optional.</span>

        {links.map((link, index) => (
          <div className="repeat-row" key={index}>
            <input
              value={link.url}
              placeholder="github.com/you"
              onChange={(event) => {
                const next = [...links];
                next[index] = { url: event.target.value };
                setLinks(next);
              }}
            />
            {/* Every row can be removed, including the last — which leaves one empty row rather
                than none, so there is always somewhere to type. */}
            <button
              type="button"
              className="icon-btn"
              aria-label="Remove this link"
              title="Remove this link"
              onClick={() => {
                const next = links.filter((_, position) => position !== index);
                setLinks(next.length ? next : [{ url: "" }]);
              }}
            >
              −
            </button>
          </div>
        ))}
      </div>

      <Error>{save.error}</Error>
      <button type="submit" className={ready ? "advance ready" : "advance"} disabled={save.busy}>
        {save.busy ? "Saving…" : "Continue"}
      </button>
    </form>
  );
}
