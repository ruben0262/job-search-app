/** The landing page, for anybody signed out: what Mahoraga gives them, in four sections, under the wheel. It
 * lies flat in its halo as the page opens and turns to face you as you scroll (owner, 2026-11-06: concept C,
 * in one purple). Written for somebody scrolling past rather than reading (owner, 2026-11-07): each section
 * a big title saying what it is, keywords first, and a picture of it moving. The motion is CSS; this file
 * says where the page is and what it has counted. */
import { Fragment, useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";

import { api, type Schema } from "../api";
import { money } from "../membership";
import { PICTURES } from "../pages/JobsPage";
import { PLACES } from "../places";
import { ThemeToggle } from "../theme";
import { Mark, Wheel } from "../wheel";
import { Alerts, Phone } from "./Demo";
import "./landing.css";

type Reach = Schema<"Reach">;
export type Pricing = Schema<"Pricing">;

const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const pointer = () => matchMedia("(hover: hover) and (pointer: fine)").matches;
const clamp = (value: number) => Math.min(1, Math.max(0, value));
/** How long a revealed thing waits before it arrives, as CSS reads it. */
const after = (seconds: number) => ({ "--d": `${seconds}s` }) as CSSProperties;

/** The sections in page order, and each one's picture in the dock: on a phone the dock is pictures alone
 * (owner, 2026-11-06), so each is the thing its section is about. */
const SECTIONS: [id: string, name: string, short: string, icon: ReactNode][] = [
  ["top", "Mahoraga", "Top", null],
  ["edge", "Why it works", "Why", <path key="i" d="M13.2 2.8L5.5 13.4h5.6l-1.3 7.8 7.7-10.6h-5.6z" />],
  ["what", "What it is", "What", <path key="i" d="M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM12 11v5.3M12 7.9h.01" />],
  ["how", "How it works", "How", <path key="i" d="M6 20.2a2.2 2.2 0 100-4.4 2.2 2.2 0 000 4.4zM18 8.2a2.2 2.2 0 100-4.4 2.2 2.2 0 000 4.4zM8.2 18h7.3a3 3 0 000-6h-7a3 3 0 010-6h7.3" />],
  ["live", "Your jobs", "Jobs", PLACES.find(([key]) => key === "jobs")?.[2]],
  ["price", "Pricing", "Price", <path key="i" d="M3.8 12.7V4.9c0-.6.5-1.1 1.1-1.1h7.8l7.6 7.6c.4.4.4 1.1 0 1.5l-7.8 7.8c-.4.4-1.1.4-1.5 0zM8.4 8.4h.01" />],
  ["start", "Get started", "Start", <path key="i" d="M12 3c2.8 1.9 4.2 5 4.2 8.8L14.6 15H9.4l-1.6-3.2C7.8 8 9.2 4.9 12 3zM12 7.6a1.6 1.6 0 110 3.2 1.6 1.6 0 010-3.2zM7.8 11.8l-2.3 2.4v3.3l3.9-2.5M16.2 11.8l2.3 2.4v3.3l-3.9-2.5M10.5 17.6c.3 1.4.8 2.4 1.5 3.1.7-.7 1.2-1.7 1.5-3.1" />],
];

/** What a person gets, in the order they get it after signing up, `often` being how often new jobs are
 * looked for ("every 15 minutes"). How the server does it is not theirs to read (owner, 2026-11-07). */
const steps = (often: string): [string, ReactNode, ReactNode][] => [
  ["Upload your CV", <>We read it and suggest the words to search with. You check them, pick your countries,
    and that is it: <strong>a few minutes, once.</strong></>, <CvArt />],
  ["Get every job that fits", <>New ones {often}, in one list, the best matches first. Each says why it is
    there. <strong>On your phone too,</strong> as a member.</>, <MatchArt />],
  ["Make your CV in one tap", <>A CV and a cover letter <strong>made for that job,</strong> from your own CV. Ready
    in seconds, as a member. Nothing made up.</>, <TapArt />],
  ["Apply, and keep track", <>Apply on the job's own site, <strong>in minutes, not hours.</strong> Then follow each
    one: the interview, the offer, the job.</>, <ApplyArt />],
];

/** Be first and be seen: what Mahoraga does about the figures above them, a card each, stacking as How it works'
 * steps do, so a reader scrolling past still reads both (owner, 2026-11-12: "short and sweet"). */
const edges = (often: string): [string, ReactNode, ReactNode][] => [
  ["New jobs in minutes, not days", <>Every job site read {often}. <strong>Apply in a job's first days,</strong> when
    hiring starts.</>, <WeekArt />],
  ["A CV made for each job, in seconds", <>From your own CV, with a cover letter, in one tap. <strong>Made to pass
    ATS checks,</strong> each one checked before you get it.</>, <AtsArt />],
];

/** A figure behind the selling points, with where it comes from: each read at its source on 2026-11-12, and
 * each said with its own scope. Claims that could not be traced to a source were left out ("4x within 10
 * minutes", "8x in 4 days"). In the order the section's words go: more people apply (in the market the page's
 * readers are in, Europe's, the UK's above all), hiring starts in the first week, and most applications are
 * never answered. */
type Figure = [figure: string, what: string, source: string, link: string];
const LINKEDIN = "https://news.linkedin.com/2026/LinkedIn-Research-Talent-2026";
const ASHBY = "https://ashbyhq.com/blog/recruiting/operationalizing-2023-application-per-job-report";
const FORTUNE = "https://fortune.com/2025/02/02/linkedin-indeed-greenhouse-job-seekers-likely-response-interviews/";
const ISE = "https://ise.org.uk/knowledge/insights/410/record_graduate_job_applications";
const FIGURES: Figure[] = [
  ["140", "applications for each UK graduate job, a record", "Institute of Student Employers, 2024", ISE],
  ["65%", "of people say finding a job has got harder", "LinkedIn survey, 13 countries incl. UK, DE, FR, Nov 2025",
   LINKEDIN],
  ["~60%", "of hirers find someone to interview in a job's first week", "LinkedIn, Jan 2026 (Hiring Pro users)", LINKEDIN],
  ["~2×", "the applications in a job's first week as in each week after", "Ashby, 13M applications", ASHBY],
  ["14M+", "applications went unanswered in one quarter of 2024", "Greenhouse, in Fortune, Feb 2025", FORTUNE],
];

/** "£0", in the membership's currency, written the reader's way. */
function nothing(currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: currency.toUpperCase(),
                                            maximumFractionDigits: 0 }).format(0);
}

/** "7 days", "a day", "12 hours": how far behind a free account is, as the app says it. */
function behind(hours: number): string {
  if (hours % 24) return `${hours} hours`;
  return hours === 24 ? "a day" : `${hours / 24} days`;
}

/** Get started: what they do once, and what then happens every day without them. */
const PATHS: [when: string, title: string, items: string[]][] = [
  ["A few minutes, once", "You set it up", ["Upload your CV", "Check the words we suggest",
                                           "Pick your countries and languages"]],
  ["Then every day", "It runs itself", ["New jobs that fit, every day", "A CV for any of them in one tap, as a member",
                                        "Alerts on your phone, as a member"]],
];

export function Landing() {
  const root = useRef<HTMLDivElement>(null);
  const [reach, setReach] = useState<Reach | null>(null);
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [at, setAt] = useState(0);
  // Counted by the server, never typed here: they went stale here before (`/stats`). Without an answer
  // the page says no number rather than an old one.
  const often = reach ? `every ${reach.every_minutes} minutes` : "all day";

  useEffect(() => {
    api.get<Reach>("/stats").then(setReach).catch(() => undefined);
    api.get<Pricing>("/pricing").then(setPricing).catch(() => undefined);
  }, []);

  // In on arrival, then each part as it is reached.
  useEffect(() => {
    const page = root.current!;
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => page.classList.add("lp-in")));
    const seen = new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("in");
      seen.unobserve(entry.target);
    }), { rootMargin: "0px 0px -8% 0px" });
    page.querySelectorAll("[data-reveal]").forEach((el) => seen.observe(el));
    return () => {
      cancelAnimationFrame(frame);
      seen.disconnect();
    };
  }, []);

  // What moves with the scroll and the pointer: the bar frosts, the halo rises to face you and leans to
  // the pointer, the dock follows the section you are in, and a card in a stack (Why it works, How it works)
  // shrinks into the shade as the next one covers it. Written straight onto the few elements that move, never
  // as a variable on the page: one there restyled every element under it on every frame (54 ms a frame on a
  // laptop, 2026-11-06).
  useEffect(() => {
    const page = root.current!;
    const halo = page.querySelector<HTMLElement>(".halo-tilt")!;
    // Each stack's cards, their shades, and where the CSS sticks each card: read once a size rather than every
    // frame. None on a short screen, where a card would be taller than the screen and the CSS lets them scroll.
    const stacks = [...page.querySelectorAll<HTMLElement>(".stack")].map((stack) => {
      const cards = [...stack.querySelectorAll<HTMLElement>(".stack-card")];
      return { stack, cards, shades: cards.map((card) => card.querySelector<HTMLElement>(".stack-shade")!),
               stuck: [] as number[] };
    });
    const motion = !still();
    const measure = () => stacks.forEach((each) => {
      const sticky = getComputedStyle(each.cards[0]!).position === "sticky";
      each.stuck = sticky ? each.cards.map((card) => parseFloat(getComputedStyle(card).top)) : [];
      if (!sticky) each.cards.forEach((card, i) => {
        card.style.transform = "";
        each.shades[i]!.style.opacity = "";
      });
    });

    // The halo glides to where the scroll and the pointer put it: a wheel moves the page a hundred pixels
    // at a time, and the halo eases the distance rather than jumping it, at the same pace at any refresh rate.
    const goal = { tilt: 70, grow: 1, turn: 0 };
    const now = { ...goal };
    const near = { tilt: 0.01, grow: 0.0001, turn: 0.01 };
    let gliding = 0;
    let last = 0;
    const glide = (time: number) => {
      const k = 1 - 0.86 ** Math.min(4, (time - last) / 16.7);
      last = time;
      let settled = true;
      for (const key of ["tilt", "grow", "turn"] as const) {
        const gap = goal[key] - now[key];
        now[key] = Math.abs(gap) < near[key] ? goal[key] : now[key] + gap * k;
        settled &&= now[key] === goal[key];
      }
      halo.style.transform = `rotateX(${now.tilt}deg) rotateY(${now.turn}deg) scale(${now.grow})`;
      gliding = settled ? 0 : requestAnimationFrame(glide);
    };
    const aim = () => {
      if (gliding) return;
      last = performance.now();
      gliding = requestAnimationFrame(glide);
    };

    let at = -1;
    let queued = false;
    const draw = () => {
      queued = false;
      const y = scrollY;
      page.classList.toggle("scrolled", y > 8);
      page.classList.toggle("past-hero", y > innerHeight * 0.5);
      if (motion) {
        const k = clamp(y / (innerHeight * 0.9));
        const eased = k * k * (3 - 2 * k);
        goal.tilt = 70 - 58 * eased;
        goal.grow = 1 + 0.18 * eased;
        aim();
        for (const { stack, cards, shades, stuck } of stacks) {
          const box = stack.getBoundingClientRect();
          if (!stuck.length || box.bottom <= 0 || box.top >= innerHeight) continue;
          const tops = cards.map((card) => card.getBoundingClientRect().top);
          cards.forEach((card, i) => {
            // How far each later card has slid over this one.
            let covered = 0;
            for (let j = i + 1; j < cards.length; j++) {
              covered += clamp(1 - (tops[j]! - stuck[j]!) / (card.offsetHeight || 1));
            }
            card.style.transform = covered ? `scale(${1 - covered * 0.045})` : "";
            shades[i]!.style.opacity = covered ? String(Math.min(0.55, covered * 0.25)) : "";
          });
        }
      }
      let here = 0;
      SECTIONS.forEach(([id], i) => {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= innerHeight * 0.4) here = i;
      });
      if (here !== at) setAt((at = here));
    };
    const queue = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(draw);
    };
    const resize = () => {
      measure();
      queue();
    };
    // The pointer leans the halo, and lights the edge of the tile it is over.
    const lean = (event: PointerEvent) => {
      goal.turn = (event.clientX / innerWidth - 0.5) * 16;
      aim();
      const lit = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-spot]") : null;
      if (!lit) return;
      const box = lit.getBoundingClientRect();
      lit.style.setProperty("--mx", `${event.clientX - box.left}px`);
      lit.style.setProperty("--my", `${event.clientY - box.top}px`);
    };
    measure();
    addEventListener("scroll", queue, { passive: true });
    addEventListener("resize", resize);
    if (motion && pointer()) addEventListener("pointermove", lean);
    queue();
    return () => {
      removeEventListener("scroll", queue);
      removeEventListener("resize", resize);
      removeEventListener("pointermove", lean);
      cancelAnimationFrame(gliding);
    };
  }, []);

  // A section link scrolls there rather than changing the address, which is the app's own route.
  const jump = (id: string) => (event: MouseEvent) => {
    event.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: still() ? "auto" : "smooth" });
  };

  return (
    <div className="landing" ref={root}>
      <div className="backdrop" aria-hidden="true" />

      <header className="lp-top">
        <a className="brand" href="#top" onClick={jump("top")}><Mark />Mahoraga</a>
        <div className="lp-actions">
          <a className="lp-link" href="#/signin">Sign in</a>
          <a className="btn btn-primary btn-sm" href="#/signup">Start free</a>
          <ThemeToggle />
        </div>
      </header>

      <Dock at={at} jump={jump} />

      <main>
        <section id="top" className="hero">
          <div className="halo" aria-hidden="true">
            <div className="halo-tilt">
              <div className="halo-spin">
                <div className="halo-ring" />
                <div className="halo-ring inner" />
                <Wheel />
              </div>
            </div>
          </div>
          <div className="hero-copy">
            <h1 className="hero-title"><Words>Mahoraga</Words></h1>
            <p className="hero-tag" style={{ "--base": "0.3s" } as CSSProperties}>
              <Words>The job search that</Words> <span className="glow"><Words from={4}>runs itself.</Words></span>
            </p>
            <p className="hero-sub" data-reveal style={after(0.6)}>
              <strong>Every job that fits you</strong>, from LinkedIn, job boards and company career pages, in one
              list. <strong>A CV for each</strong> in one tap. You apply on the job's own site, in minutes.
            </p>
            <div className="hero-ctas" data-reveal style={after(0.7)}>
              <a className="btn btn-primary" href="#/signup">Start free <Arrow /></a>
              <a className="btn btn-glass" href="#/signin">Sign in</a>
              <a className="btn btn-glass" href="#how" onClick={jump("how")}>How it works</a>
            </div>
          </div>
          {reach ? (
            <dl className="reach">
              <div><dt><Count to={reach.sources} /></dt><dd>Job sources</dd></div>
              {/* Each left out while it counts nothing: a harvest that has stopped is not a quiet week. */}
              {reach.company_boards ? (
                <div><dt><AtLeast n={reach.company_boards} /></dt><dd>Company career pages</dd></div>
              ) : null}
              {reach.jobs_week ? <div><dt><AtLeast n={reach.jobs_week} /></dt><dd>New jobs this week</dd></div> : null}
              {/* Every `every_minutes`, all day and night: said in the sections below to the minute. */}
              <div><dt>24/7</dt><dd>Rapid checks</dd></div>
            </dl>
          ) : null}
        </section>

        {/* Why it gets people interviews (owner, 2026-11-12): there first, with a CV made for the job. */}
        <section id="edge" className="section">
          <div className="head center" data-reveal>
            <h2>Be first. <span className="glow">Be seen.</span></h2>
            <p className="lp-lede">
              More people apply for every job, and hiring starts in its first week. Mahoraga gets you there
              <strong> early,</strong> with <strong>a CV made for the job.</strong>
            </p>
          </div>
          <Figures of={FIGURES} />
          <Stack cards={edges(often)} />
          {/* The rest of what it does for them, a picture each, so the point lands without reading. */}
          <div className="edge-row">
            <article className="tile edge-mini" data-reveal data-spot>
              <SitesArt boards={reach?.company_boards ?? 0} />
              <h3>Straight from company sites</h3>
              <p>Not only job boards: jobs from companies' own career pages.</p>
            </article>
            <article className="tile edge-mini" data-reveal data-spot style={after(0.06)}>
              <FilterArt />
              <h3>Filters that do the reading</h3>
              <p>Your words find jobs, red words hide them. Never the same job twice.</p>
            </article>
            <article className="tile edge-mini" data-reveal data-spot style={after(0.12)}>
              <TrackArt />
              <h3>Every application tracked</h3>
              <p>Applied, interview, offer: one tap each. No spreadsheet.</p>
            </article>
          </div>
        </section>

        <section id="what" className="section">
          <div className="head center" data-reveal>
            <h2>Every job site. <span className="glow">One list for you.</span></h2>
            <p className="lp-lede">
              It reads them all for you and keeps only the jobs that fit. No more checking sites one by one.
            </p>
          </div>
          <Flow often={often} />
        </section>

        <section id="how" className="section">
          <div className="head center" data-reveal>
            <h2>From sign‑up <span className="glow">to applied.</span></h2>
            <p className="lp-lede">What you get, in the order you get it.</p>
          </div>
          <Stack cards={steps(often)} numbered />
        </section>

        <section id="live" className="section">
          <div className="head" data-reveal>
            <h2>See it <span className="glow">working.</span></h2>
            <p className="lp-lede">
              This is your Jobs page. Tap <strong>Make my CV</strong> and watch one written for this job.
            </p>
          </div>
          <div className="live-grid">
            <Phone />
            <div data-reveal style={after(0.12)}>
              <h3 className="side-title">New jobs on your phone</h3>
              <p className="lp-lede">
                By Telegram, in your browser, or one email each morning. Each with a link straight to its CV.
              </p>
              <Alerts />
            </div>
          </div>
        </section>

        {/* Free, a week behind; or a member, first (owner, 2026-11-12). What the cards say is set in Admin. */}
        <section id="price" className="section">
          <div className="head center" data-reveal>
            <h2>Start free. <span className="glow">Upgrade to be first.</span></h2>
          </div>
          {pricing ? <PriceCards pricing={pricing} /> : null}
        </section>

        <section id="start" className="section">
          <div className="head center" data-reveal>
            <h2>Start in <span className="glow">a few minutes.</span></h2>
            <p className="lp-lede">Two steps. You do the first one, once.</p>
          </div>
          <div className="paths">
            {PATHS.map(([when, title, items], i) => (
              <article key={title} className="tile path" data-reveal data-spot style={after(i * 0.08)}>
                <div className="path-head">
                  <span className="path-num" aria-hidden="true">{i + 1}</span>
                  <div>
                    <p className="path-when">{when}</p>
                    <h3>{title}</h3>
                  </div>
                </div>
                <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
              </article>
            ))}
          </div>
          <div className="cta" data-reveal>
            <Wheel ticks={0} />
            <p>Your first jobs are there the moment you finish.</p>
            <a className="btn btn-primary btn-lg" href="#/signup">Start free <Arrow /></a>
          </div>
        </section>
      </main>

      <footer className="lp-foot">
        <div className="foot-halo" aria-hidden="true"><div><Wheel /></div></div>
        <p className="foot-name" aria-hidden="true">Mahoraga</p>
        <div className="foot-row">
          <span>The job search that runs itself</span>
          <a href="#/signin">Sign in</a>
          <a href="#/privacy">Privacy</a>
          <a href="https://rubenlekli.com" target="_blank" rel="noreferrer">rubenlekli.com</a>
        </div>
      </footer>
    </div>
  );
}

/** The sections, a pill of light on the one you are in: at the foot of a phone, where the app keeps its
 * tab bar, in pictures; at the head of a laptop, in words. */
function Dock({ at, jump }: { at: number; jump: (id: string) => (event: MouseEvent) => void }) {
  const dock = useRef<HTMLElement>(null);
  const pill = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const place = () => {
      const link = dock.current?.querySelectorAll("a")[at];
      if (!link || !pill.current) return;
      pill.current.style.translate = `${link.offsetLeft}px 0`;
      pill.current.style.width = `${link.offsetWidth}px`;
    };
    place();
    addEventListener("resize", place);
    void document.fonts?.ready.then(place);
    return () => removeEventListener("resize", place);
  }, [at]);
  return (
    <nav className="dock" aria-label="Sections" ref={dock}>
      <span className="dock-pill" ref={pill} />
      {SECTIONS.map(([id, name, short, icon], i) => (
        <a key={id} href={`#${id}`} onClick={jump(id)} className={i === at ? "on" : undefined}
           aria-label={name} aria-current={i === at ? "true" : undefined}>
          {icon ? <svg viewBox="0 0 24 24" aria-hidden="true">{icon}</svg> : <Mark />}
          {icon ? <><span className="short">{short}</span><span className="long">{name}</span></> : null}
        </a>
      ))}
    </nav>
  );
}

/** The two cards, Free and Member, as the landing page shows them, Admin previews them (`preview`: their buttons
 * go nowhere) and the plan page offers them (`onChoose`: their buttons pick). The words are the operator's
 * (`pricing_cards`); the price is Stripe's, the free days `trial_days` and the delay `free_delay_hours`, filled
 * in here, so no wording can promise what the site does not keep. */
export function PriceCards({ pricing, preview = false, onChoose, busy = false }: {
  pricing: Pricing;
  preview?: boolean;
  onChoose?: (kind: "free" | "member") => void;
  busy?: boolean;
}) {
  const { cards, plan, trial_days: days, free_delay_hours: delay } = pricing;
  const per = plan && (plan.interval_count === 1 ? plan.interval : `${plan.interval_count} ${plan.interval}s`);
  return (
    <div className="price-room">
      <div className="price-cards">
        <PriceCard card={cards.free} href="#/signup" preview={preview} busy={busy}
                   onChoose={onChoose && (() => onChoose("free"))}
                   amount={plan ? <><strong>{nothing(plan.currency)}</strong><span>for good</span></> : null}
                   under={`Jobs ${behind(delay)} after members see them`} />
        <PriceCard card={cards.member} href="#/signup?plan=member" preview={preview} lit busy={busy}
                   onChoose={onChoose && (() => onChoose("member"))}
                   badge={days ? `${days} days free` : null}
                   amount={plan ? <><strong>{money(plan.amount, plan.currency)}</strong><span>/{per}</span></> : null}
                   under={days ? (plan ? `after your ${days}-day free trial` : `The first ${days} days free`) : null} />
      </div>
    </div>
  );
}

/** One card: its name, what it costs, what it gives and leaves out, and its button at the foot. */
function PriceCard({ card, href, preview, onChoose, busy, lit = false, badge = null, amount, under }: {
  card: Pricing["cards"]["free"];
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
      <div>
        <h3 className="price-name">{card.name}</h3>
        {card.tagline ? <p className="price-tagline">{card.tagline}</p> : null}
      </div>
      {amount || under ? (
        <div>
          {amount ? <p className="price-amount">{amount}</p> : null}
          {under ? <p className="price-after">{under}</p> : null}
        </div>
      ) : null}
      <ul className="price-list">
        {card.benefits.map((line, i) => (
          <li key={`in${i}`}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="10" /><path d="M6 10.4l2.6 2.6 5.6-5.6" /></svg>
            {line}
          </li>
        ))}
        {card.left_out.map((line, i) => (
          <li key={`out${i}`} className="out">
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="10" /><path d="M7 7l6 6M13 7l-6 6" /></svg>
            <span className="unseen">Not included: </span>{line}
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

/** The figures behind the claims, each with its source, linked: one band, the numbers big enough to read at a
 * scroll. */
function Figures({ of }: { of: Figure[] }) {
  return (
    <dl className="figures" data-reveal>
      {of.map(([figure, what, source, link]) => (
        <div key={what}>
          <dt>{figure}</dt>
          <dd>{what}<a href={link} target="_blank" rel="noreferrer">{source}</a></dd>
        </div>
      ))}
    </dl>
  );
}

/** Cards that stick as the page scrolls, each sliding over the one before (`Landing`'s scroll effect shades
 * it): a title, a line and a picture each, and the step's number where they are steps. */
function Stack({ cards, numbered = false }: { cards: [string, ReactNode, ReactNode][]; numbered?: boolean }) {
  return (
    <div className="stack">
      {cards.map(([name, text, art], i) => (
        <article key={name} className="stack-card" style={{ "--i": i } as CSSProperties}>
          <div className="stack-shade" aria-hidden="true" />
          <div>
            {numbered ? <span className="stack-num" aria-hidden="true">{i + 1}</span> : null}
            <h3>{name}</h3>
            <p>{text}</p>
          </div>
          <div className="stack-art" aria-hidden="true">{art}</div>
        </article>
      ))}
    </div>
  );
}

/** Applications a week over a job's first month, as Ashby counted them (the first week about twice each later
 * one), and you on its first day. Its bars grow once it is reached. */
function WeekArt() {
  return (
    <div className="week-art" data-reveal>
      <span className="week-you">You, day 1</span>
      {[100, 50, 48, 46].map((tall, i) => (
        <i key={i} style={{ "--h": `${tall}%`, "--i": i } as CSSProperties}><b>Week {i + 1}</b></i>
      ))}
    </div>
  );
}

/** The hiring systems whose career pages it reads directly, Europe's among them, and how many companies' pages
 * (`/stats`, never typed here; left out while it counts none). */
function SitesArt({ boards }: { boards: number }) {
  return (
    <div className="mini-art sites-art" aria-hidden="true">
      {boards ? <span className="sites-count"><AtLeast n={boards} /> career pages</span> : null}
      <div>
        {["Personio", "Recruitee", "Greenhouse", "Workable", "Lever", "SmartRecruiters"].map((name, i) => (
          <span key={name} style={{ "--i": i } as CSSProperties}>{name}</span>
        ))}
      </div>
    </div>
  );
}

/** What the filters do: green words in, red words out, the places, the way of working and the languages. */
function FilterArt() {
  return (
    <div className="mini-art filter-art" aria-hidden="true">
      <span className="in">nurse</span><span className="in">care</span><span className="out">night</span>
      <span>Germany</span><span>Remote</span><span>English</span>
    </div>
  );
}

/** An application followed: applied and interview reached, the offer next. */
function TrackArt() {
  return (
    <ol className="mini-art track-art" aria-hidden="true">
      {["Applied", "Interview", "Offer"].map((step, i) => <li key={step} className={i < 2 ? "done" : ""}>{step}</li>)}
    </ol>
  );
}

/** A CV read the way an applicant tracking system reads it: its headings found, ticked in turn once it is
 * reached. */
function AtsArt() {
  return (
    <div className="ats-art" data-reveal>
      <div className="ats-paper">
        <b />
        {["Profile", "Experience", "Skills"].map((heading, i) => (
          <span key={heading} style={{ "--i": i } as CSSProperties}>{heading}<em>✓</em></span>
        ))}
      </div>
      <span className="ats-badge"><svg viewBox="0 0 20 20"><path d="M5.5 10.4l3 3 6-6.4" /></svg>Passes ATS checks</span>
    </div>
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

/** Words that arrive one after another out of a blur. Read whole: the text is the words, spaced. */
function Words({ children, from = 0 }: { children: string; from?: number }) {
  return children.split(" ").map((word, i) => (
    <Fragment key={i}>
      {i ? " " : null}
      <span className="word-in" style={{ "--i": from + i } as CSSProperties}>{word}</span>
    </Fragment>
  ));
}

/** A number that counts up to itself once, as the page opens. */
function Count({ to }: { to: number }) {
  const [shown, setShown] = useState(() => (still() ? to : 0));
  useEffect(() => {
    if (still()) return;
    const start = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const k = clamp((now - start) / 1500);
      setShown(Math.round(to * (1 - (1 - k) ** 4)));
      if (k < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [to]);
  return <>{shown.toLocaleString("en")}</>;
}

/** A count as a round number it is at least, so a big one reads at a glance and stays true: 43 is "40+",
 * 341 "340+", 72,345 "72,000+". Under ten, itself. */
function AtLeast({ n }: { n: number }) {
  const step = n < 10 ? 1 : n < 100 ? 5 : 10 ** (Math.floor(Math.log10(n)) - 1);
  return <><Count to={Math.floor(n / step) * step} />{n < 10 ? "" : "+"}</>;
}

/** What it is, drawn: the job sites, what is left out, and the list that is yours, jobs travelling from one
 * to the next on a loop. Said in words under each part, for anybody not looking at the picture. */
function Flow({ often }: { often: string }) {
  return (
    <div className="flow" data-reveal>
      <div className="flow-step">
        <div className="flow-art flow-sources" aria-hidden="true">
          {["LinkedIn", "Job boards", "Company career pages"].map((name, i) => (
            <span key={name} style={{ "--i": i } as CSSProperties}><i />{name}</span>
          ))}
        </div>
        <h3>Every job site</h3>
        <p>LinkedIn, job boards and company career pages, read {often}.</p>
      </div>
      <FlowOn />
      <div className="flow-step">
        <div className="flow-art flow-filter" aria-hidden="true">
          <Mark />
          <div className="left-out">
            {["The same job twice", "A country you can't work in", "Your red words"].map((why, i) => (
              <s key={why} style={{ "--i": i } as CSSProperties}>{why}<i /></s>
            ))}
          </div>
        </div>
        <h3>Only what fits</h3>
        <p>Your words, your countries, your languages. Never the same job twice.</p>
      </div>
      <FlowOn />
      <div className="flow-step">
        <div className="flow-art flow-list" aria-hidden="true">
          {[["Backend Engineer", "backend engineer"], ["Data Engineer", "python · sql"],
            ["Python Developer", "python"]].map(([title, why], i) => (
            <span key={title} style={{ "--i": i } as CSSProperties}><b>{title}</b><small>Why: {why}</small></span>
          ))}
        </div>
        <h3>One list for you</h3>
        <p>The best matches first, each saying which of your words found it.</p>
      </div>
    </div>
  );
}

/** Between two parts of the picture: jobs travelling on to the next. */
function FlowOn() {
  return <div className="flow-on" aria-hidden="true"><i /><i /><i /></div>;
}

/** Step 1: their CV, and the words read from it, green to find jobs with and red to hide them. */
function CvArt() {
  return (
    <div className="cv-art">
      <span className="cv-file">
        <svg viewBox="0 0 24 24"><path d="M7 3.5h6.5L18 8v12.5H7zM13.5 3.5V8H18M9.8 12.5h5M9.8 16h5" /></svg>
        my-cv.pdf
      </span>
      <span className="cv-said">We suggest</span>
      <div className="cv-words">
        {[["backend engineer", "finds"], ["python", "finds"], ["unpaid", "hides"], ["commission only", "hides"]].map(
          ([word, kind], i) => <span key={word} className={kind} style={{ "--i": i } as CSSProperties}>{word}</span>)}
      </div>
    </div>
  );
}

/** Step 2: new jobs arriving, the words that found each marked. */
function MatchArt() {
  return (
    <div className="match-art">
      <span className="match-new">3 new jobs</span>
      <div className="match"><b><mark>Backend Engineer</mark></b><small>Acme Robotics · Berlin · Why: backend engineer</small></div>
      <div className="match"><b><mark>Python</mark> Developer</b><small>Northwind · Remote · Why: python</small></div>
    </div>
  );
}

/** Step 3: one tap, and the CV written. */
function TapArt() {
  return (
    <div className="tap-art">
      <div className="one-tap">
        <svg className="ring" viewBox="0 0 40 40">
          <circle className="track" cx="20" cy="20" r="17" />
          <circle className="fill" cx="20" cy="20" r="17" pathLength={1} />
        </svg>
        <span>Make my CV</span>
      </div>
      <div className="tap-paper"><b>Jordan Ellery</b><i /><i /><i /><i /></div>
    </div>
  );
}

/** Step 4: out to the job's own site, and the application followed to the offer. */
function ApplyArt() {
  return (
    <div className="apply-art">
      <span className="apply-on">Apply on LinkedIn{PICTURES.apply}</span>
      <ol className="apply-steps">
        {["Applied", "Interview", "Offer"].map((step, i) => (
          <li key={step} style={{ "--i": i } as CSSProperties}><i />{step}</li>
        ))}
      </ol>
    </div>
  );
}

function Arrow() {
  return (
    <svg className="arrow" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
