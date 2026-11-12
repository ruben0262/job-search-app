/** Admin → Settings: what the landing page's two pricing cards say, Free and Member (`pricing_cards`, owner
 * 2026-11-12), saved together and shown beside the form as a visitor will see them. The price is Stripe's, the
 * free days are Free trial and the delay is the free accounts' setting: the cards fill them in, so nothing
 * typed here names them. */
import { useState } from "react";

import { api } from "../api";
import { useKept } from "../kept";
import { PriceCards, type Pricing } from "../landing/Landing";
import { Error, useAction } from "../ui";

type Cards = Pricing["cards"];
type Kind = keyof Cards;
type Card = Cards[Kind];

/** What the server allows a list (`pricing_routes.PricingCard`). */
const MOST_LINES = 12;

export function AdminPricing() {
  const kept = useKept<Pricing>("/pricing");
  // What is being typed, until it is saved; null is the saved cards.
  const [draft, setDraft] = useState<Cards | null>(null);
  const [kind, setKind] = useState<Kind>("free");
  const [saved, setSaved] = useState(false);
  const save = useAction(async (cards: Cards) => {
    const body = await api.put<Pricing>("/admin/pricing", cards);
    kept.set(() => body);
    setDraft(null);
    setSaved(true);
  });
  if (!kept.data) return kept.failed ? <p className="error">Could not read the pricing.</p> : null;

  const cards = draft ?? kept.data.cards;
  const card = cards[kind];
  const edit = (change: Partial<Card>) => {
    setDraft({ ...cards, [kind]: { ...card, ...change } });
    setSaved(false);
  };

  return (
    <div className="card pricing-admin">
      <h2 style={{ marginTop: 0 }}>Pricing on the landing page</h2>
      <p className="hint">
        The two cards visitors choose between. The price comes from Stripe, the free days from Free trial and
        how far behind Free is from its own setting, so don&apos;t write those here: they change by themselves.
      </p>
      <div className="pricing-edit">
        <form onSubmit={(event) => {
          event.preventDefault();
          void save.run(cards);
        }}>
          <div className="toggles" role="group" aria-label="Card" style={{ marginBottom: "1rem" }}>
            {(["free", "member"] as const).map((each) => (
              <button key={each} type="button" className="toggle" aria-pressed={kind === each}
                      onClick={() => setKind(each)}>{each === "free" ? "Free" : "Member"}</button>
            ))}
          </div>
          <label className="field">
            <span className="label">Name</span>
            <input value={card.name} maxLength={40} required onChange={(event) => edit({ name: event.target.value })} />
          </label>
          <label className="field">
            <span className="label">Line under the name</span>
            <input value={card.tagline} maxLength={120} onChange={(event) => edit({ tagline: event.target.value })} />
          </label>
          <Lines legend="Included" name="Included" lines={card.benefits} least={1}
                 onChange={(benefits) => edit({ benefits })} />
          <Lines legend="Not included (crossed out)" name="Not included" lines={card.left_out} least={0}
                 onChange={(left_out) => edit({ left_out })} />
          <label className="field">
            <span className="label">Button</span>
            <input value={card.button} maxLength={40} required onChange={(event) => edit({ button: event.target.value })} />
          </label>
          <label className="field">
            <span className="label">Small print under the button</span>
            <textarea value={card.note} maxLength={280} rows={3} onChange={(event) => edit({ note: event.target.value })} />
          </label>
          <div className="pricing-save">
            <button type="submit" disabled={!draft || save.busy}>{save.busy ? "Saving…" : "Save both cards"}</button>
            {draft ? <button type="button" className="secondary" onClick={() => setDraft(null)}>Undo changes</button> : null}
            {saved ? <span className="muted small" role="status">Saved. The landing page shows it now.</span> : null}
          </div>
          <Error>{save.error}</Error>
        </form>
        {/* As a visitor sees them: the landing page's own cards, in the landing page's styles. */}
        <div className="landing pricing-preview" aria-label="Preview">
          <PriceCards pricing={{ ...kept.data, cards }} preview />
        </div>
      </div>
    </div>
  );
}

/** A list of lines, each its own box with a ×, and a button for one more. */
function Lines({ legend, name, lines, least, onChange }: {
  legend: string;
  name: string;
  lines: string[];
  least: number;
  onChange: (lines: string[]) => void;
}) {
  return (
    <fieldset className="field pricing-lines">
      <legend className="label">{legend}</legend>
      {lines.map((each, i) => (
        <div key={i} className="pricing-line">
          <input value={each} maxLength={120} required aria-label={`${name}, line ${i + 1}`}
                 onChange={(event) => onChange(lines.map((line, j) => (j === i ? event.target.value : line)))} />
          <button type="button" className="secondary" aria-label={`Remove ${name.toLowerCase()} line ${i + 1}`}
                  disabled={lines.length <= least} onClick={() => onChange(lines.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      {lines.length < MOST_LINES ? (
        <button type="button" className="secondary" onClick={() => onChange([...lines, ""])}>Add a line</button>
      ) : null}
    </fieldset>
  );
}
