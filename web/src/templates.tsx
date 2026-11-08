/** How your CVs and cover letters look: the one of each you use, and a sheet to pick another (owner,
 * 2026-11-07). The most popular first, then each job sector with its looks; one look can be in several.
 *
 * Each picture is the look drawn in abstract shapes, not a document, at a quarter of its size: laid out by
 * the look's own stylesheet, with nothing written in it. Each look is named, and nothing more.
 */
import { useCallback, useState } from "react";

import { api, type Schema } from "./api";
import { useKept } from "./kept";
import { Error, Sheet, useAction } from "./ui";

type Templates = Schema<"Templates">;
type Kind = keyof Templates;

/** What this card reads, as `kept.ts` keeps it. */
export const TEMPLATES = "/templates";

const SAY: Record<Kind, { what: string; change: string; pick: string; now: string }> = {
  cv: { what: "Your CVs", change: "Change how your CVs look", pick: "Pick how your CVs look",
        now: "Your CVs will now look like" },
  cover: { what: "Your cover letters", change: "Change how your cover letters look",
           pick: "Pick how your cover letters look", now: "Your cover letters will now look like" },
};

export function TemplatePicks() {
  const { data, set } = useKept<Templates>(TEMPLATES);
  const [open, setOpen] = useState<Kind | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  if (!data) return <p className="muted small">Loading…</p>;
  return (
    <>
      <p className="small muted">Every CV and cover letter we make for you is made in the look you pick.</p>
      <div className="looks">
        {(["cv", "cover"] as const).map((kind) => {
          const shelf = data[kind];
          const chosen = shelf.templates.find((each) => each.id === shelf.chosen);
          return (
            <div key={kind} className="look">
              {chosen ? <Picture html={chosen.picture} /> : null}
              <div className="look-about">
                <span className="small muted">{SAY[kind].what}</span>
                <strong className="look-name">{chosen?.name}</strong>
                <button type="button" className="secondary" aria-label={SAY[kind].change}
                        onClick={() => {
                          setSaid(null);
                          setOpen(kind);
                        }}>
                  Change
                </button>
              </div>
            </div>
          );
        })}
      </div>
      {said ? <p className="small" role="status">{said}</p> : null}
      {open ? (
        <Picker kind={open} shelf={data[open]} onClose={() => setOpen(null)}
                onPicked={(next, name) => {
                  set(() => next);
                  setSaid(`${SAY[open].now} ${name}.`);
                  setOpen(null);
                }} />
      ) : null}
    </>
  );
}

function Picker({ kind, shelf, onClose, onPicked }: {
  kind: Kind;
  shelf: Templates[Kind];
  onClose: () => void;
  onPicked: (next: Templates, name: string) => void;
}) {
  const named = new Map(shelf.templates.map((each) => [each.id, each]));
  const pick = useAction(async (id: string) => {
    onPicked(await api.put<Templates>(`/templates/${kind}`, { id }), named.get(id)?.name ?? id);
  });
  return (
    <Sheet title={SAY[kind].pick} onClose={onClose}>
      <p className="small muted">Tap one to use it. Every one is easy to read, for people and for the software job sites use.</p>
      <Error>{pick.error}</Error>
      {shelf.groups.map((group) => (
        <section key={group.title} className="look-group" aria-label={group.title}>
          <h3>{group.title}</h3>
          <div className="look-row">
            {group.templates.map((id) => {
              const look = named.get(id);
              const inUse = id === shelf.chosen;
              return look ? (
                <button key={id} type="button" className="look-card" aria-pressed={inUse} disabled={pick.busy}
                        onClick={() => void pick.run(id)}>
                  <Picture html={look.picture} />
                  <span className="look-name">{look.name}{inUse ? <span className="look-on"> · In use</span> : null}</span>
                </button>
              ) : null;
            })}
          </div>
        </section>
      ))}
    </Sheet>
  );
}

/** The look, drawn from the shelf already read: the server's own picture of it, never anybody's data,
 * parsed into an `<html>` of its own inside a shadow root. There the look's stylesheet applies exactly as
 * in a page of its own, and the site's cannot reach it (`.picture` stops what it would inherit). Each
 * picture was a frame of its own until 2026-11-08: a document and a request each, ~40 in a sheet, 1-2 s
 * measured on a laptop and 5-10 s as the owner saw it; drawn here, all 39 take ~75 ms. The card around it is the button. */
function Picture({ html }: { html: string }) {
  const draw = useCallback((host: HTMLSpanElement | null) => {
    if (!host) return;
    const page = document.createElement("html");
    page.innerHTML = html;
    (host.shadowRoot ?? host.attachShadow({ mode: "open" })).replaceChildren(page);
  }, [html]);
  return (
    <span className="thumb" aria-hidden="true">
      <span className="picture" ref={draw} />
    </span>
  );
}
