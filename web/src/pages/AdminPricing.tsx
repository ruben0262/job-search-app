/** Admin → Subscriptions: every word on the landing page's pricing cards, Free, Starter and Member, saved together
 * and shown beside the form as a visitor will see them (owner, 2026-11-12, 2026-11-16 and 2026-11-17: "make sure
 * the bullet points and the benefits as well as the crossed out sections appear in admin all the time"): each
 * card's name, band, button and small print (`pricing_cards`), and the lines of what the plans give and lack
 * (`pricing_lines`), all filled in, never blank to be typed. Which card ticks a line and which crosses it out follows the plans, fixed in code, and the
 * preview is worked out by the server as the landing page's cards are (`/admin/pricing/preview`). A price is
 * Stripe's, and the price it is reduced from and the free days are their own settings (above): the cards fill
 * them in, so nothing typed here names them. */
import { useEffect, useState } from "react";

import { ApiError, api, type Schema } from "../api";
import { useKept } from "../kept";
import { PriceCards, type Pricing } from "../landing/Landing";
import { Error, useAction } from "../ui";

type Editor = Schema<"PricingEditor">;
type Words = Schema<"PricingWords">;
type Lines = Words["lines"];
type Line = Lines["wait"];
type Kind = keyof Words["cards"];
type Card = Words["cards"][Kind];

const PLAN_NAMES: Record<string, string> = { free: "Free", starter: "Starter", member: "Member" };

/** What each paid area is, as this page names it; one the page does not know is named by its key. */
const AREA_NAMES: Record<string, string> = {
  alerts: "Alerts", match_scores: "Match score", cvs: "CVs", covers: "Cover letters", ats_checks: "ATS check",
  add_job: "Add a job", team_access: "Members' chat, shown once its link is set",
};

/** The widest a line can be and stay one line on every card, in pixels at the cards' 16px: from a 320px phone,
 * where the words step down to 15px (`landing.css`), to three cards side by side at 1024px (measured
 * 2026-11-16). */
const ONE_LINE_PX = 204;
let ruler: CanvasRenderingContext2D | null = null;

function tooLong(text: string): boolean {
  ruler ??= document.createElement("canvas").getContext("2d");
  if (!ruler) return false;
  ruler.font = '16px "Inter Variable", ui-sans-serif, system-ui, sans-serif';
  return ruler.measureText(text).width > ONE_LINE_PX;
}

export function AdminPricing() {
  const editor = useKept<Editor>("/admin/pricing");
  const shown = useKept<Pricing>("/pricing");
  // What is being changed, until it is saved; null is the words saved.
  const [draft, setDraft] = useState<Words | null>(null);
  // The cards as the draft would make them, and why they cannot be, if they cannot.
  const [preview, setPreview] = useState<Pricing | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind>("free");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setProblem(null);
    if (!draft) {
      setPreview(null);
      return;
    }
    const { wait, now, everyone, areas } = draft.lines;
    if ([wait, now, ...everyone, ...Object.values(areas).flat()].some((line) => !line.text.trim() || !line.more.trim())) {
      setProblem("Fill in every line, and what its question mark says.");
      return;
    }
    const timer = window.setTimeout(() => {
      api.post<Pricing>("/admin/pricing/preview", draft).then(setPreview).catch((caught) => setProblem(
        caught instanceof ApiError ? caught.detail : "The preview did not load. Try again."));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [draft]);
  const save = useAction(async (words: Words) => {
    const body = await api.put<Pricing>("/admin/pricing", words);
    shown.set(() => body);
    editor.set((now) => now && { ...now, ...words });
    setDraft(null);
    setSaved(true);
  });
  if (!editor.data || !shown.data) {
    return editor.failed || shown.failed ? <p className="error">Could not read the pricing.</p> : null;
  }

  const { ticked_on: tickedOn, waits } = editor.data;
  const words: Words = draft ?? { cards: editor.data.cards, lines: editor.data.lines };
  const change = (next: Words) => {
    setDraft(next);
    setSaved(false);
  };
  const card = words.cards[kind];
  const edit = (field: Partial<Card>) => change({ ...words, cards: { ...words.cards, [kind]: { ...card, ...field } } });
  const lines = words.lines;
  const setLines = (field: Partial<Lines>) => change({ ...words, lines: { ...lines, ...field } });
  // {wait} measured as the longest wait it becomes, and said as each one.
  const longest = Object.values(waits).sort((a, b) => b.length - a.length)[0] ?? "7 days";
  const waiting = Object.entries(waits).map(([plan, wait]) => `${wait} on ${PLAN_NAMES[plan] ?? plan}`).join(", ");
  const names = (plans: string[]) => plans.map((plan) => PLAN_NAMES[plan] ?? plan).join(" and ");

  return (
    <div className="card pricing-admin">
      <h2 style={{ marginTop: 0 }}>Pricing on the landing page</h2>
      <p className="hint">
        The cards visitors choose between, every word of them. Each line is worded once: it is ticked on the plans
        that have it and crossed out on the others, which follows the plans themselves. Prices come from Stripe and
        the free days from their own setting, so don&apos;t write those here. Starter&apos;s card shows to visitors
        once it has a price at Stripe.
      </p>
      <div className="pricing-edit">
        <form onSubmit={(event) => {
          event.preventDefault();
          void save.run(words);
        }}>
          <h3>Each card</h3>
          <div className="toggles" role="group" aria-label="Card" style={{ marginBottom: "1rem" }}>
            {(["free", "starter", "member"] as const).map((each) => (
              <button key={each} type="button" className="toggle" aria-pressed={kind === each}
                      onClick={() => setKind(each)}>{PLAN_NAMES[each]}</button>
            ))}
          </div>
          <label className="field">
            <span className="label">Name</span>
            <input value={card.name} maxLength={40} required onChange={(event) => edit({ name: event.target.value })} />
          </label>
          <label className="field">
            <span className="label">Band under the price</span>
            <input value={card.highlight} maxLength={40} placeholder="Price locked for life"
                   onChange={(event) => edit({ highlight: event.target.value })} />
          </label>
          <label className="field">
            <span className="label">Button</span>
            <input value={card.button} maxLength={40} required onChange={(event) => edit({ button: event.target.value })} />
          </label>
          <label className="field">
            <span className="label">Small print under the button</span>
            <textarea value={card.note} maxLength={280} rows={3} onChange={(event) => edit({ note: event.target.value })} />
          </label>

          <h3>The lines on the cards</h3>
          <LineGroup title="How soon a new job shows: a plan that waits" max={1} lines={[lines.wait]} wait={longest}
                     hint={`{wait} becomes each plan's wait: ${waiting || "none waits now"}.`}
                     onChange={([wait]) => setLines({ wait: wait! })} />
          <LineGroup title="How soon a new job shows: a plan that doesn't wait" max={1} lines={[lines.now]}
                     onChange={([now]) => setLines({ now: now! })} />
          <LineGroup title="A paid card's first line" max={1} lines={[lines.everything]}
                     wait={[words.cards.free.name, words.cards.starter.name].sort((a, b) => b.length - a.length)[0]}
                     placeholder="{plan}"
                     hint="{plan} becomes the card before it: Free on Starter's card, Starter on Member's."
                     onChange={([everything]) => setLines({ everything: everything! })} />
          <LineGroup title="What every account has" max={8} lines={lines.everyone}
                     hint="Ticked on Free. The cards after it say “Everything in Free” instead."
                     onChange={(everyone) => setLines({ everyone })} />
          {Object.entries(tickedOn).map(([area, plans]) => (
            <LineGroup key={area} title={AREA_NAMES[area] ?? area} max={4} lines={lines.areas[area] ?? []}
                       hint={`Ticked on ${names(plans)}; crossed out on the others.`}
                       onChange={(each) => setLines({ areas: { ...lines.areas, [area]: each } })} />
          ))}

          <div className="pricing-save">
            <button type="submit" disabled={!draft || save.busy || problem !== null}>
              {save.busy ? "Saving…" : "Save the cards"}
            </button>
            {draft ? <button type="button" className="secondary" onClick={() => setDraft(null)}>Undo changes</button> : null}
            {saved ? <span className="muted small" role="status">Saved. The landing page shows it now.</span> : null}
          </div>
          <Error>{problem ?? save.error}</Error>
        </form>
        {/* As a visitor sees them: the landing page's own cards, in the landing page's styles. */}
        <div className="landing pricing-preview" aria-label="Preview">
          <PriceCards pricing={preview ?? { ...shown.data, cards: words.cards }} preview />
        </div>
      </div>
    </div>
  );
}

/** The lines of one kind, each its words and what its question mark says, with a line added or taken away. */
function LineGroup({ title, hint, lines, wait, placeholder = "{wait}", max, onChange }: {
  title: string;
  hint?: string;
  lines: Line[];
  /** What the placeholder becomes at the longest, to measure the line by. */
  wait?: string;
  /** The word in the line that the cards fill in: {wait}, or {plan}. */
  placeholder?: string;
  max: number;
  onChange: (lines: Line[]) => void;
}) {
  const put = (at: number, line: Line) => onChange(lines.map((each, i) => (i === at ? line : each)));
  return (
    <fieldset className="line-group">
      <legend>{title}</legend>
      {hint ? <p className="hint">{hint}</p> : null}
      {lines.map((line, at) => {
        const long = tooLong(wait ? line.text.replaceAll(placeholder, wait) : line.text);
        return (
          <div key={at} className="line-edit">
            <label className="field">
              <span className="label">Line</span>
              <input value={line.text} maxLength={40} required aria-invalid={long || undefined}
                     onChange={(event) => put(at, { ...line, text: event.target.value })} />
              {long ? <span className="hint line-long">Too long for one line on a phone: shorten it.</span> : null}
            </label>
            <label className="field">
              <span className="label">What its question mark says</span>
              <textarea value={line.more} maxLength={280} rows={3} required
                        onChange={(event) => put(at, { ...line, more: event.target.value })} />
            </label>
            {lines.length > 1 ? (
              <button type="button" className="secondary small" onClick={() => onChange(lines.filter((_, i) => i !== at))}>
                Remove this line
              </button>
            ) : null}
          </div>
        );
      })}
      {lines.length < max ? (
        <button type="button" className="secondary small" onClick={() => onChange([...lines, { text: "", more: "" }])}>
          + Add a line
        </button>
      ) : null}
    </fieldset>
  );
}
