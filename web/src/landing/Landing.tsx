/** The landing page, for anybody signed out: the promise first, then what Mahoraga is, how it works for
 * them, the app itself, the plans, the start, and last a question for a person, under the wheel. It lies
 * flat in its halo as the page opens and turns to face you as you scroll (owner, 2026-11-06: concept C, in
 * one purple). Written for somebody scrolling past rather than reading (owner, 2026-11-07): each section a
 * big title saying what it is, keywords first, and a picture of it moving. Who it speaks to and why each
 * line says what it says: `docs/saas/MESSAGING.md` (owner, 2026-11-17). The motion is CSS; this file says
 * where the page is and what it has counted. */
import { Fragment, useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";

import { api, type Schema } from "../api";
import { PICTURES } from "../pages/JobsPage";
import { LegalLinks } from "../pages/LegalPage";
import { PLACES } from "../places";
import { SocialMark, useSocials } from "../socials";
import { ThemeToggle } from "../theme";
import { Error, Field, useAction } from "../ui";
import { Mark, Wheel } from "../wheel";
import { Alerts, Phone } from "./Demo";
import { Arrow, PriceCards, type Pricing } from "./pricecards";
import { Reviews } from "./Reviews";
import "./landing.css";

type Reach = Schema<"Reach">;

const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const pointer = () => matchMedia("(hover: hover) and (pointer: fine)").matches;
const clamp = (value: number) => Math.min(1, Math.max(0, value));
/** How long a revealed thing waits before it arrives, as CSS reads it. */
const after = (seconds: number) => ({ "--d": `${seconds}s` }) as CSSProperties;

/** The sections in page order, and each one's picture in the dock: on a phone the dock is pictures alone
 * (owner, 2026-11-06), so each is the thing its section is about. "Why it works" is gone (owner, 2026-11-17):
 * its promise leads the hero, and what it did for people is How it works. */
const SECTIONS: [id: string, name: string, short: string, icon: ReactNode][] = [
  ["top", "Mahoraga", "Top", null],
  ["what", "What it is", "What", <path key="i" d="M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM12 11v5.3M12 7.9h.01" />],
  ["how", "How it works", "How", <path key="i" d="M6 20.2a2.2 2.2 0 100-4.4 2.2 2.2 0 000 4.4zM18 8.2a2.2 2.2 0 100-4.4 2.2 2.2 0 000 4.4zM8.2 18h7.3a3 3 0 000-6h-7a3 3 0 010-6h7.3" />],
  ["live", "See it working", "See", PLACES.find(([key]) => key === "jobs")?.[2]],
  ["price", "Pricing", "Price", <path key="i" d="M3.8 12.7V4.9c0-.6.5-1.1 1.1-1.1h7.8l7.6 7.6c.4.4.4 1.1 0 1.5l-7.8 7.8c-.4.4-1.1.4-1.5 0zM8.4 8.4h.01" />],
  ["start", "Get started", "Start", <path key="i" d="M12 3c2.8 1.9 4.2 5 4.2 8.8L14.6 15H9.4l-1.6-3.2C7.8 8 9.2 4.9 12 3zM12 7.6a1.6 1.6 0 110 3.2 1.6 1.6 0 010-3.2zM7.8 11.8l-2.3 2.4v3.3l3.9-2.5M16.2 11.8l2.3 2.4v3.3l-3.9-2.5M10.5 17.6c.3 1.4.8 2.4 1.5 3.1.7-.7 1.2-1.7 1.5-3.1" />],
];

/** How it works, as what it does for them rather than how they sign up (owner, 2026-11-17: "the benefits of
 * how the app works"; that night, "mention saving time"): a tile each, in the order a job goes through
 * them, found, alerted, scored, kept, written for and followed. No plan is named on them, the cards say who
 * gets what ("people can tell by the offers"); no way of sending an alert ("the user will just get
 * notifications is enough"); and never how often the sites are read ("confidential"). Owner, that late night. */
const perks = (ats: Reach["ats"] | undefined): [string, ReactNode, ReactNode][] => [
  ["New jobs in minutes, not days", <>Jobs show up the moment they're posted on the sites. Apply before the crowd
    does.</>, <WeekArt />],
  // What an alert follows, as the Alerts page makes one (`pages/AlertsPage.tsx`: words, companies, places).
  ["Instant alerts on your phone", <>No more refreshing job sites. Follow a company, a city or a kind of job, and
    know the moment one is posted.</>, <AlertArt />],
  ["A match score on every job", <>Every job is scored out of 10 for how well it fits you. Filter by score,
    country, remote or job site, in one tap.</>, <ScoreArt />],
  ["Save jobs for later", <>Not ready yet? Tap the bookmark and the job waits for you, every detail kept, until
    you apply or it closes.</>, <BookmarkArt />],
  // The figure is our own tests', and only for the CV wording it was measured on (`/stats`): without it the
  // page claims no rate. Said alone (owner, 2026-11-17 night: no count of checks, nothing on where it is
  // from), on a line of its own so an eye running down the tile lands on it; "official" is the owner's
  // (that late night: "we've done official research on the ATS checks").
  ["A CV made for each job, in seconds", <>One tap: a CV and a cover letter for that job, from your own CV.
    Nothing made up, nothing to rewrite.{" "}<span className="proof">{ats
      ? <strong>{passRate(ats)}% passed all official ATS checks.</strong>
      : <><strong>Made to pass official ATS checks,</strong> each one checked before you get it.</>}</span></>,
    <AtsArt />],
  ["Every application tracked", <>Applied, interview, offer, the job: one tap each. No more Excel or Google
    Sheets to keep up.</>, <TrackArt />],
];

/** "99": the share of CVs that passed every check, never rounded up to all of them. */
function passRate(ats: NonNullable<Reach["ats"]>): number {
  return ats.passed === ats.of ? 100 : Math.min(99, Math.round((100 * ats.passed) / ats.of));
}

/** Get started: what they do once, and what then happens every day without them. */
const PATHS: [when: string, title: string, items: string[]][] = [
  ["A few minutes, once", "You set it up", ["Upload your CV", "Check the words we suggest",
                                           "Pick your countries and languages"]],
  ["Then every day", "It runs itself", ["New jobs that fit, every day", "A CV for any of them in one tap",
                                        "Alerts on your phone"]],
];

export function Landing() {
  const root = useRef<HTMLDivElement>(null);
  const [reach, setReach] = useState<Reach | null>(null);
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [at, setAt] = useState(0);
  const socials = useSocials();

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
    // Each part's loops run only while it is on the screen. Off it, they were drawn again on every frame for
    // nobody: on a phone at the top of the page, the step dots far below were half the page's work (2026-11-18).
    const awake = new IntersectionObserver((entries) => entries.forEach((entry) => {
      entry.target.classList.toggle("asleep", !entry.isIntersecting);
    }));
    page.querySelectorAll("main > section").forEach((el) => awake.observe(el));
    return () => {
      cancelAnimationFrame(frame);
      seen.disconnect();
      awake.disconnect();
    };
  }, []);

  // What moves with the scroll and the pointer: the bar frosts, the halo rises to face you and leans to
  // the pointer, and the dock follows the section you are in. Written straight onto the few elements that
  // move, never as a variable on the page: one there restyled every element under it on every frame (54 ms a
  // frame on a laptop, 2026-11-06). Nothing sticks as you scroll any more: How it works' cards used to stack
  // one over the next, a screen each, and a page read at a fast scroll lays them side by side (owner,
  // 2026-11-17).
  useEffect(() => {
    const page = root.current!;
    const halo = page.querySelector<HTMLElement>(".halo-tilt")!;
    const motion = !still();

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
    addEventListener("scroll", queue, { passive: true });
    addEventListener("resize", queue);
    if (motion && pointer()) addEventListener("pointermove", lean);
    queue();
    return () => {
      removeEventListener("scroll", queue);
      removeEventListener("resize", queue);
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
        <a className="brand" href="#top" aria-label="Mahoraga" onClick={jump("top")}>
          <Mark /><span className="brand-name">Mahoraga</span>
        </a>
        <div className="lp-actions">
          <a className="lp-link" href="/signin">Sign in</a>
          <a className="btn btn-primary btn-sm" href="/signup">Start free</a>
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
            {/* The promise, in the biggest words on the page (owner, 2026-11-17): a visitor gives the top of it
                three seconds, and the name tells them nothing yet; it is in the bar. One line of three words
                (that night: "way too long ... powerful, punchy, one liner"): the owner's "be first" and "be
                seen" in one, being seen what this audience is missing and first how they get it. A phone breaks
                it before "first.", never inside "Get seen". */}
            <h1 className="hero-title">
              <span className="hero-line"><Words>Get seen</Words></span>{" "}
              <span className="hero-line glow"><Words from={2}>first.</Words></span>
            </h1>
            <p className="hero-tag" style={{ "--base": "0.3s" } as CSSProperties}>
              <Words>The job search that runs itself.</Words>
            </p>
            {/* Why being first and seen matters, in the owner's words (2026-11-12), the figures behind them taken
                off the page (owner, 2026-11-17) and kept here. More people apply: a record 140 applications for
                each UK graduate job (Institute of Student Employers, 2024,
                https://ise.org.uk/knowledge/insights/410/record_graduate_job_applications). Hiring starts in the
                first week: ~60% of hirers find someone to interview in it (LinkedIn, Jan 2026, Hiring Pro users,
                https://news.linkedin.com/2026/LinkedIn-Research-Talent-2026), and a job draws ~2x the
                applications in it as in each week after (Ashby, 13M applications,
                https://ashbyhq.com/blog/recruiting/operationalizing-2023-application-per-job-report). */}
            <p className="hero-sub" data-reveal style={after(0.6)}>
              More people apply for every job, and hiring starts in its first week. Mahoraga gets you
              there <strong>early,</strong> with <strong>a CV made for the job.</strong>
            </p>
            {/* One button to start and one to see how (owner, 2026-10-28: one main button a screen); signing in
                is the bar's. Under them, what starting asks: no card. No plan is named outside the cards (owner,
                2026-11-17 late night). */}
            <div className="hero-ctas" data-reveal style={after(0.7)}>
              <a className="btn btn-primary" href="/signup">Start free <Arrow /></a>
              <a className="btn btn-glass" href="#how" onClick={jump("how")}>How it works</a>
            </div>
            <p className="lp-note" data-reveal style={after(0.75)}>No card needed.</p>
          </div>
        </section>

        {/* Every source the harvest reads, a logo each, in two rows drifting opposite ways: the numbers that were
            under the hero, shown instead (owner, 2026-11-24). The server's one list (`/stats`), so it cannot fall
            behind what is read; drawn when it comes, so not one of the reveals counted as the page opens. */}
        <section id="sources" className="section source-strip" aria-labelledby="sources-title">
          {/* Where the jobs come from and how soon, in two beats as the page's other titles go (owner, 2026-11-24:
              "something more powerful", and never "these" or "them"). A phrase is never broken. */}
          <h2 id="sources-title" data-reveal>
            <span className="keep">Straight from the source.</span>{" "}
            <span className="glow keep">The moment a job goes live.</span>
          </h2>
          {reach ? <Sources boards={reach.boards} /> : null}
        </section>

        <section id="what" className="section">
          <div className="head center" data-reveal>
            <h2>Every job site. <span className="glow">One list for you.</span></h2>
            <p className="lp-lede">
              It reads them all for you and keeps only the jobs that fit. No more checking sites one by one.
            </p>
          </div>
          <Flow />
        </section>

        {/* What it does for them, not how they sign up (owner, 2026-11-17): the pains named once, in the line
            under the title, and a tile answering each, laid side by side to be read at a fast scroll. */}
        <section id="how" className="section">
          <div className="head center" data-reveal>
            {/* A phrase is never broken: the title breaks between them, wherever the width puts it. */}
            <h2><span className="keep">You apply.</span> <span className="glow keep">We do the rest.</span></h2>
            <p className="lp-lede">Hours of searching, rewriting and tracking, done for you.</p>
          </div>
          <div className="perks">
            {perks(reach?.ats).map(([name, text, art], i) => (
              <article key={name} className="tile perk" data-reveal data-spot style={after((i % 2) * 0.08)}>
                <div className="perk-art" aria-hidden="true">{art}</div>
                <h3>{name}</h3>
                <p>{text}</p>
              </article>
            ))}
          </div>
          {/* Whose AI writes them, since they bring their own (owner, 2026-11-17 night: "worth mentioning it for
              sure"): under the tiles, below the CV's on a laptop, rather than in its tile, which it made the
              tallest of its row and left the two beside it with empty feet. */}
          <p className="lp-note">CVs and cover letters are written with your own ChatGPT or Claude account.</p>
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
                A notification the moment a job you want is posted, with a link straight to its CV.
              </p>
              <Alerts />
            </div>
          </div>
          <Reviews />
        </section>

        {/* Free, a week behind; Starter, sooner and alerted; or a member, first and seen (owner, 2026-11-12 and
            2026-11-16). Every word on the cards is set in Admin; which card ticks what is the server's. The title
            is what the plans buy, a better search than everybody else's, then the call to join (owner,
            2026-11-17: "compete with a larger pool", "CTA for them to join us"; that night, "the grind remains
            but we beat the way most people are searching for jobs", so it no longer says the grind is
            skipped). The saving, the price kept for life and the free days are said on the cards themselves,
            once (owner, 2026-11-16: "only keep them inside the offers"). */}
        <section id="price" className="section">
          <div className="head center" data-reveal>
            {/* A phrase is never broken: the title breaks between them, wherever the width puts it. */}
            <h2>
              <span className="keep">Search smarter.</span> <span className="keep">Beat the crowd.</span>{" "}
              <span className="glow keep">Join now.</span>
            </h2>
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
          {/* What it asks of them, nothing to learn or install, and where it goes with them (owner, 2026-11-17
              night: the old line "doesn't quite make sense"; "easy to use without any tutorials", "open it on
              your laptop, or phone wherever you are"). */}
          <div className="cta" data-reveal>
            <Wheel ticks={0} />
            <p>No complex setup, nothing to install. Your jobs on your phone or laptop, wherever you are.</p>
            <a className="btn btn-primary btn-lg" href="/signup">Start free <Arrow /></a>
            <p className="lp-note">No card needed.</p>
          </div>
        </section>

        <Ask />
      </main>

      {/* The owner's own site is no longer linked (owner, 2026-11-17: their name off the site and the
          project); the way to a person is. */}
      <footer className="lp-foot">
        <div className="foot-halo" aria-hidden="true"><div><Wheel /></div></div>
        <p className="foot-name" aria-hidden="true">Mahoraga</p>
        <div className="foot-row">
          <span>The job search that runs itself</span>
          <a href="/signin">Sign in</a>
          <LegalLinks />
          <a href="#ask" onClick={jump("ask")}>Contact</a>
        </div>
        {/* Everywhere else the site is, a picture each in the foot's own colour (owner, 2026-11-23). */}
        {socials.length ? (
          <div className="foot-social">
            {socials.map((social) => (
              <a key={social.network} href={social.url} target="_blank" rel="noreferrer" aria-label={social.name}
                 title={social.name}>
                <SocialMark social={social} plain />
              </a>
            ))}
          </div>
        ) : null}
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

/** Applications a week over a job's first month, as Ashby counted them (the first week about twice each later
 * one; the source is beside the hero's words), and you on its first day. Its bars grow once it is reached. */
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

/** A match score on each job, out of 10, in the colour the Jobs page gives it (`.tag.match`, red at 1 to green
 * at 10), under the filters that are on, as the chips under its search say them: two that fit, and one under
 * the 7s and up asked for, leaving the list as the filter takes it. */
function ScoreArt() {
  return (
    <div className="score-art">
      <span className="score-filters"><i>Match 7+</i><i>United Kingdom</i></span>
      {([["Team Leader", 9], ["Supervisor", 8], ["Night Picker", 5]] as const).map(([title, fit]) => (
        <span key={title} className={fit < 7 ? "score-row low" : "score-row"}>
          <b>{title}</b><i className="tag match" style={{ "--fit": fit } as CSSProperties}>{fit}/10</i>
        </span>
      ))}
    </div>
  );
}

/** An application followed, as Applied draws it: the job, then applied, the interview and the offer on its
 * track, each lit in turn and round again. Still, the first two stand lit. */
function TrackArt() {
  return (
    <div className="track-art">
      <b>Warehouse Team Leader</b>
      <small>Acme Logistics</small>
      <ol>
        {["Applied", "Interview", "Offer"].map((step, i) => (
          <li key={step} className={i < 2 ? "done" : undefined} style={{ "--i": i } as CSSProperties}>
            <i aria-hidden="true" />{step}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A job arriving on a phone's lock screen, as a browser alert says it (`services/notifier/card.py`: the title,
 * then the name of the alert that caught it, the company and the place), over the ones before it. It drops in,
 * rests and comes again; still, it rests. */
function AlertArt() {
  return (
    <div className="alert-art">
      <span className="alert-clock">9:41</span>
      <span className="alert-note">
        <i><Mark /></i>
        <span><small>Mahoraga<em>now</em></small><b>Warehouse Team Leader</b>
          <small>Team leader jobs · Acme Logistics · Leeds</small></span>
      </span>
    </div>
  );
}

/** A job kept for later, as the Jobs page's card keeps it: the bookmark in its corner filling as it is
 * tapped, and where it went. Still, it stands kept. */
function BookmarkArt() {
  return (
    <div className="bookmark-art">
      <span className="bookmark-card">
        <b>Care Assistant</b>
        <small>Sunrise Care · Bristol</small>
        <i /><i />
        {/* Kept, it fills: the filled one fades in over the outline, drawn by the compositor. */}
        <span className="bookmark-mark" aria-hidden="true">{PICTURES.bookmark}{PICTURES.bookmark}</span>
      </span>
      <span className="bookmark-kept">Kept in Bookmarks</span>
    </div>
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
      <span className="ats-badge"><svg viewBox="0 0 20 20"><path d="M5.5 10.4l3 3 6-6.4" /></svg>Passes official ATS checks</span>
    </div>
  );
}

/** A question before signing up, into the inbox the Help sheet's form writes to (`help.tsx`, `/contact`): a
 * person reads it and replies to the address given (owner, 2026-11-17 night: "the same place where we have
 * inside the app, so users can freely ask any questions they like"). Two fields and one button, and the
 * receipt where the form was. */
function Ask() {
  const discord = useSocials().find((social) => social.network === "discord");
  const [email, setEmail] = useState("");
  const [question, setQuestion] = useState("");
  const [sent, setSent] = useState<Schema<"ContactReceipt"> | null>(null);
  const send = useAction(async () => {
    setSent(await api.post<Schema<"ContactReceipt">>("/contact", { email: email.trim(), message: question.trim() }));
    setEmail("");
    setQuestion("");
  });
  const ready = email.includes("@") && question.trim().length >= 10;
  return (
    <section id="ask" className="section">
      <div className="head center" data-reveal>
        <h2>Got a question? <span className="glow">Ask us.</span></h2>
        <p className="lp-lede">A person reads every message and replies by email.</p>
      </div>
      <div className="tile ask" data-reveal data-spot>
        {sent ? (
          <div className="ask-sent" role="status">
            <h3>Message received</h3>
            <p>We'll reply to {sent.email}.</p>
            <p>Your reference <code>{sent.reference}</code></p>
            <button type="button" className="btn btn-glass" onClick={() => setSent(null)}>Ask something else</button>
          </div>
        ) : (
          <form onSubmit={(event) => {
            event.preventDefault();
            if (ready) void send.run();
          }}>
            <Field label="Your email">
              <input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </Field>
            <Field label="Your question">
              <textarea rows={5} maxLength={4000} value={question}
                        onChange={(event) => setQuestion(event.target.value)} />
            </Field>
            <Error>{send.error}</Error>
            <button type="submit" className="btn btn-primary" disabled={!ready || send.busy}>
              {send.busy ? "Sending…" : "Send my question"}
            </button>
            <p className="lp-note">We use your email only to reply.</p>
          </form>
        )}
      </div>
      {/* The people who build it, and each other, a tap away (owner, 2026-11-18). Drawn when its link has come,
          after the reveals are counted: it is not one of them. */}
      {discord ? (
        <div className="tile community">
          <span className="social-chip" style={{ color: discord.colour }}>
            <SocialMark social={discord} />
          </span>
          <div>
            <h3>Join us on Discord</h3>
            <p>Tell us what you think, ask for what you want next, and talk with the people who build Mahoraga.</p>
          </div>
          <a className="btn btn-glass" href={discord.url} target="_blank" rel="noreferrer">
            <SocialMark social={discord} />{discord.join}
          </a>
        </div>
      ) : null}
    </section>
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

/** The sources in two rows, the top drifting right and the one under it left (owner, 2026-11-24), each its logo
 * and its name. A row is its sources twice over, so it has no end as it drifts; the second time is not read out. */
function Sources({ boards }: { boards: Reach["boards"] }) {
  const rows = [boards.filter((_board, i) => i % 2 === 0), boards.filter((_board, i) => i % 2 === 1)];
  return rows.map((row, i) => (
    <div key={i} className="drift-row" style={{ "--n": row.length } as CSSProperties}>
      <div className={i === 0 ? "drift-track rightward" : "drift-track"}>
        {[false, true].map((again) => (
          <ul key={String(again)} className="drift-set" aria-hidden={again || undefined}>
            {row.map((board) => (
              <li key={board.key} className="strip-source">
                <img src={`/boards/${board.key}.png`} alt="" width="32" height="32" decoding="async" />
                {board.name}
              </li>
            ))}
          </ul>
        ))}
      </div>
    </div>
  ));
}

/** What it is, drawn: the job sites, what is left out, and the list that is yours, jobs travelling from one
 * to the next on a loop. Said in words under each part, for anybody not looking at the picture. */
function Flow() {
  return (
    <div className="flow" data-reveal>
      <div className="flow-step">
        <div className="flow-art flow-sources" aria-hidden="true">
          {["LinkedIn", "Job boards", "Company career pages"].map((name, i) => (
            <span key={name} style={{ "--i": i } as CSSProperties}><i />{name}</span>
          ))}
        </div>
        <h3>Every job site</h3>
        <p>LinkedIn, job boards and company career pages, each new job the moment it's posted.</p>
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
          {[["Staff Nurse", "nurse"], ["Care Home Nurse", "nurse · care"],
            ["Care Coordinator", "care"]].map(([title, why], i) => (
            <span key={title} style={{ "--i": i } as CSSProperties}><b>{title}</b><small>Your words: {why}</small></span>
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
