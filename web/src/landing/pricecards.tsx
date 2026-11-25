/** The cards a plan is chosen from, Free, Starter and Member: on the landing page, on the page between the
 * email link and setting up, on the upgrade page, and in Admin's preview of them. Apart from the landing page
 * itself, which is a file of its own that only a visitor is sent (`App.tsx`): these are for somebody signed in
 * too. Their looks are the landing page's (`landing.css`), which comes with them. */
import type { ReactNode } from "react";

import type { Schema } from "../api";
import { money } from "../membership";
import { Explain } from "../ui";
import "./landing.css";

export type Pricing = Schema<"Pricing">;

type Paid = "starter" | "member";
type Card = Pricing["cards"]["free"];

const LOCK = <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10.5V8a5 5 0 0110 0v2.5M5.5 10.5h13v10h-13z" /></svg>;
/** A card's tick. */
const TICK = <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="10" /><path d="M6 10.4l2.6 2.6 5.6-5.6" /></svg>;

/** How much less a plan costs than the price it is shown as reduced from, in whole per cent; null with no
 * such price, or one that is not higher. Worked out here from the two prices, so the card cannot say a saving
 * its own figures do not make. */
export function saving(amount: number, was: number | undefined): number | null {
  return was && was > amount ? Math.round((1 - amount / was) * 100) : null;
}

/** The cards, Free, Starter and Member, as the landing page shows them, Admin previews them (`preview`: their
 * buttons go nowhere) and the plan page offers them (`onChoose`: their buttons pick). What each says of its plan
 * is the server's (`lines`), worked out from the plans fixed in code and how long each waits for a job; the
 * rest of the words are the operator's (`pricing_cards`); a price is Stripe's, the price it is reduced from
 * `price_was` and the free days each plan's own (the membership's `trial_days`; Starter has none, owner
 * 2026-11-17), all filled in here, so no wording can promise what the site does not keep. Starter is shown once it has a price at Stripe (and always in Admin's preview, to be worded before
 * it has one). */
export function PriceCards({ pricing, preview = false, onChoose, busy = false }: {
  pricing: Pricing;
  preview?: boolean;
  onChoose?: (kind: "free" | Paid) => void;
  busy?: boolean;
}) {
  const { cards, lines, plan, starter, trial_days: days, was } = pricing;
  const three = Boolean(starter) || preview;
  // What a paid card says of its price: the one it is reduced from crossed out, the saving, the price itself.
  const cost = (kind: Paid, of: Pricing["plan"]) => {
    if (!of) return null;
    const before = was[kind];
    const off = saving(of.amount, before);
    const per = of.interval_count === 1 ? of.interval : `${of.interval_count} ${of.interval}s`;
    return (
      <>
        {off && before ? (
          <span className="price-was">
            <span className="unseen">Was </span><s>{money(before, of.currency)}</s>
            <em>{off}% off</em>
          </span>
        ) : null}
        <strong>{money(of.amount, of.currency)}</strong><span>/{per}</span>
      </>
    );
  };
  // A plan's own free days, under its price: Stripe's plan says them once it has a price; before, the
  // membership's setting says the membership's, and Starter has none.
  const trial = (of: Pricing["plan"], free: number) =>
    (free ? (of ? `after your ${free}-day free trial` : `The first ${free} days free`) : null);
  return (
    <div className="price-room">
      <div className={three ? "price-cards three" : "price-cards"}>
        <PriceCard card={cards.free} said={lines.free} href="/signup" preview={preview} busy={busy}
                   onChoose={onChoose && (() => onChoose("free"))}
                   amount={plan ? <><strong>{nothing(plan.currency)}</strong><span>for good</span></> : null}
                   under={null} />
        {three ? (
          <PriceCard card={cards.starter} said={lines.starter} href="/signup?plan=starter" preview={preview}
                     busy={busy} onChoose={onChoose && (() => onChoose("starter"))}
                     amount={cost("starter", starter)} under={trial(starter, starter?.trial_days ?? 0)} />
        ) : null}
        <PriceCard card={cards.member} said={lines.member} href="/signup?plan=member" preview={preview} lit
                   busy={busy} onChoose={onChoose && (() => onChoose("member"))}
                   badge={(plan?.trial_days ?? days) ? `${plan?.trial_days ?? days} days free` : null}
                   amount={cost("member", plan)} under={trial(plan, plan?.trial_days ?? days)} />
      </div>
    </div>
  );
}

/** One card: its name, what it costs, what it gives and leaves out, and its button at the foot. A paid card
 * begins with everything in the card before it (owner, 2026-11-16: "so people know what we are referring to";
 * 2026-11-17: "Everything in Free plan", the server's line in the operator's words), then how soon it sees a
 * job, so the three read across at a glance, then what it adds; then what its plan lacks, crossed out. Each
 * line is a few words, with a question mark that says more. */
function PriceCard({ card, said, href, preview, onChoose, busy, lit = false, badge = null, amount, under }: {
  card: Card;
  said: Pricing["lines"]["free"];
  href: string;
  preview: boolean;
  onChoose?: () => void;
  busy: boolean;
  lit?: boolean;
  badge?: string | null;
  amount: ReactNode;
  under: string | null;
}) {
  const button = lit ? "btn btn-primary price-cta" : "btn btn-glass price-cta";
  return (
    <article className={lit ? "tile price-card lit" : "tile price-card"} data-spot>
      {badge ? <span className="price-badge"><Gift />{badge}</span> : null}
      <h3 className="price-name">{card.name}</h3>
      {amount || under ? (
        <div>
          {amount ? <p className="price-amount">{amount}</p> : null}
          {/* What the price is for, in a band read at a glance: "Price locked for life" (`highlight`). */}
          {card.highlight && amount ? <p className="price-band">{LOCK}{card.highlight}</p> : null}
          {under ? <p className="price-after">{under}</p> : null}
        </div>
      ) : null}
      <ul className="price-list">
        {said.everything ? (
          <li className="everything">
            {TICK}
            <span><span className="line">{said.everything.text}</span><Explain text={said.everything.more} about={said.everything.text} /></span>
          </li>
        ) : null}
        {said.gives.map((line, i) => (
          <li key={`in${i}`}>
            {TICK}
            <span><span className="line">{line.text}</span><Explain text={line.more} about={line.text} /></span>
          </li>
        ))}
        {/* A bonus (owner, 2026-11-17: "people tend to react to them great"): given apart, in a box of its own,
            marked so at a glance, after what the plan gives. */}
        {(said.bonus ?? []).map((line, i) => (
          <li key={`bonus${i}`} className="bonus">
            <span className="bonus-tag"><Gift />Bonus</span>
            {TICK}
            <span><span className="line">{line.text}</span><Explain text={line.more} about={line.text} /></span>
          </li>
        ))}
        {said.lacks.map((line, i) => (
          <li key={`out${i}`} className="out">
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="10" /><path d="M7 7l6 6M13 7l-6 6" /></svg>
            <span><span className="unseen">Not included: </span><span className="line">{line.text}</span><Explain text={line.more} about={line.text} /></span>
          </li>
        ))}
      </ul>
      {onChoose ? <button type="button" className={button} disabled={busy} onClick={onChoose}>{card.button}<Arrow /></button>
        : preview ? <span className={button}>{card.button}<Arrow /></span>
          : <a className={button} href={href}>{card.button}<Arrow /></a>}
      {card.note ? <p className="price-note">{card.note}</p> : null}
    </article>
  );
}

/** A present: what a free trial is. */
function Gift() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.5 11h15v9h-15zM3.5 7.5h17V11h-17zM12 7.5V20M12 7.5C10.6 4.2 7 4.3 7 6.2c0 1.3 2.4 1.3 5 1.3zm0 0c1.4-3.3 5-3.2 5-1.3 0 1.3-2.4 1.3-5 1.3z" />
    </svg>
  );
}

/** "£0", in the membership's currency, written the reader's way. */
function nothing(currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: currency.toUpperCase(),
                                            maximumFractionDigits: 0 }).format(0);
}

/** The arrow on a button that leads somewhere. */
export function Arrow() {
  return (
    <svg className="arrow" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
