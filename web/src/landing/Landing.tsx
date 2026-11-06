/** The landing page, for anybody signed out: what Mahoraga is, in four sections, under the wheel. It lies
 * flat in its halo as the page opens and turns to face you as you scroll (owner, 2026-11-06: concept C,
 * in one purple). The motion is CSS; this file says where the page is and what it has counted. */
import { Fragment, useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";

import { api, type Schema } from "../api";
import { PLACES } from "../places";
import { ThemeToggle } from "../theme";
import { Mark, Wheel } from "../wheel";
import { Demo } from "./Demo";
import "./landing.css";

type Reach = Schema<"Reach">;

const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const pointer = () => matchMedia("(hover: hover) and (pointer: fine)").matches;
const clamp = (value: number) => Math.min(1, Math.max(0, value));
/** How long a revealed thing waits before it arrives, as CSS reads it. */
const after = (seconds: number) => ({ "--d": `${seconds}s` }) as CSSProperties;

/** The sections in page order, and each one's picture in the dock: on a phone the dock is pictures alone
 * (owner, 2026-11-06), so each is the thing its section is about. */
const SECTIONS: [id: string, name: string, short: string, icon: ReactNode][] = [
  ["top", "Mahoraga", "Top", null],
  ["what", "What it is", "What", <path key="i" d="M12 3.5a8.5 8.5 0 110 17 8.5 8.5 0 010-17zM12 11v5.3M12 7.9h.01" />],
  ["how", "How it works", "How", <path key="i" d="M6 20.2a2.2 2.2 0 100-4.4 2.2 2.2 0 000 4.4zM18 8.2a2.2 2.2 0 100-4.4 2.2 2.2 0 000 4.4zM8.2 18h7.3a3 3 0 000-6h-7a3 3 0 010-6h7.3" />],
  ["live", "Your jobs", "Jobs", PLACES.find(([key]) => key === "jobs")?.[2]],
  ["start", "Get started", "Start", <path key="i" d="M12 3c2.8 1.9 4.2 5 4.2 8.8L14.6 15H9.4l-1.6-3.2C7.8 8 9.2 4.9 12 3zM12 7.6a1.6 1.6 0 110 3.2 1.6 1.6 0 010-3.2zM7.8 11.8l-2.3 2.4v3.3l3.9-2.5M16.2 11.8l2.3 2.4v3.3l-3.9-2.5M10.5 17.6c.3 1.4.8 2.4 1.5 3.1.7-.7 1.2-1.7 1.5-3.1" />],
];

/** The six steps, `often` being how often new jobs are collected ("every 15 minutes"). */
const steps = (often: string): [string, ReactNode][] => [
  ["Collect", <>{often.charAt(0).toUpperCase() + often.slice(1)}, new jobs from job boards, company career pages and LinkedIn.</>],
  ["Clean", <>Duplicates are removed, so you never see the same job twice. Each job is read in full before it is
    shown, and jobs in languages you do not work in, or in places you cannot work, are left out.</>],
  ["Match", <>Your green words find jobs and your red words hide them. The more of your green words a job has,
    the higher it sits, and every job shows which of your words found it.</>],
  ["Tell", <>Matches wait on your Jobs page. If you want them on your phone too, connect your own Telegram bot
    in three steps, turn on browser notifications, or get one email each morning.</>],
  ["Build", <>Tap <code>Make my CV</code> or <code>Make my cover letter</code>. It is built from your own CV with
    what fits the job first, and ready to download in seconds.</>],
  ["Refine", <>Change your words any time. Save a new CV and we suggest new words to find jobs with, and new words
    to hide.</>],
];

export function Landing() {
  const root = useRef<HTMLDivElement>(null);
  const [reach, setReach] = useState<Reach | null>(null);
  const [at, setAt] = useState(0);
  // Counted by the server, never typed here: they went stale here before (`/stats`). Without an answer
  // the page says no number rather than an old one.
  const often = reach ? `every ${reach.every_minutes} minutes` : "all day";

  useEffect(() => {
    api.get<Reach>("/stats").then(setReach).catch(() => undefined);
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
  // the pointer, the dock follows the section you are in, and a step's card shrinks into the shade as the
  // next one covers it. Written straight onto the few elements that move, never as a variable on the page:
  // one there restyled every element under it on every frame (54 ms a frame on a laptop, 2026-11-06).
  useEffect(() => {
    const page = root.current!;
    const halo = page.querySelector<HTMLElement>(".halo-tilt")!;
    const stack = page.querySelector<HTMLElement>(".stack")!;
    const cards = [...stack.querySelectorAll<HTMLElement>(".stack-card")];
    const shades = cards.map((card) => card.querySelector<HTMLElement>(".stack-shade")!);
    const motion = !still();
    // Where the CSS sticks each card, read once a size rather than every frame. None on a short screen,
    // where a card would be taller than the screen and the CSS lets them scroll instead.
    let stuck: number[] = [];
    const measure = () => {
      const sticky = getComputedStyle(cards[0]!).position === "sticky";
      stuck = sticky ? cards.map((card) => parseFloat(getComputedStyle(card).top)) : [];
      if (!sticky) cards.forEach((card, i) => {
        card.style.transform = "";
        shades[i]!.style.opacity = "";
      });
    };

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
        const box = stack.getBoundingClientRect();
        if (stuck.length && box.bottom > 0 && box.top < innerHeight) {
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
          <a className="btn btn-primary btn-sm" href="#/signup">Create account</a>
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
            <p className="pill"><i />The job-search radar · <b>live</b></p>
            <h1 className="hero-title"><Words>Mahoraga</Words></h1>
            <p className="hero-tag" style={{ "--base": "0.3s" } as CSSProperties}>
              <Words>The job search that</Words> <span className="glow"><Words from={4}>runs itself.</Words></span>
            </p>
            <p className="hero-sub" data-reveal style={after(0.6)}>
              Choose the words you want and the words you don't. It reads job boards, company career pages and
              LinkedIn {often}, and makes your CV and cover letter in one tap. You apply on the job's own site,
              in minutes, not hours.
            </p>
            <div className="hero-ctas" data-reveal style={after(0.7)}>
              <a className="btn btn-primary" href="#/signup">Create account <Arrow /></a>
              <a className="btn btn-glass" href="#/signin">Sign in</a>
              <a className="btn btn-glass" href="#how" onClick={jump("how")}>How it works</a>
            </div>
          </div>
          {reach ? (
            <dl className="reach">
              <div><dt><Count to={reach.sources} /></dt><dd>Job sources</dd></div>
              <div><dt><Count to={reach.every_minutes} /> min</dt><dd>Between checks</dd></div>
              <div><dt><Count to={reach.company_boards} /></dt><dd>Company boards</dd></div>
              <div><dt><Count to={100} />%</dt><dd>Yours alone</dd></div>
            </dl>
          ) : null}
        </section>

        <section id="what" className="section">
          <div className="head center" data-reveal>
            <p className="pill"><i />01 · What it is</p>
            <h2>You apply. <span className="glow">It does the rest.</span></h2>
          </div>
          <div className="bento">
            <article className="tile" data-reveal data-spot>
              <p className="tile-k">All day, every day</p>
              <h3>{reach ? `New jobs every ${reach.every_minutes} minutes` : "New jobs all day"}</h3>
              <p>From job boards, from company career pages, and from LinkedIn, each read in full before it is shown.</p>
              {reach ? <Found runs={reach.found} minutes={reach.every_minutes} /> : null}
            </article>
            <article className="tile" data-reveal data-spot style={after(0.06)}>
              <p className="tile-k">You decide what you see</p>
              <h3>Green words find jobs. Red words hide them.</h3>
              <WordDemo />
            </article>
            <article className="tile" data-reveal data-spot style={after(0.1)}>
              <p className="tile-k">Nothing is hidden</p>
              <h3>Every job says which of your words it matched</h3>
              <div className="tile-demo">
                <div className="match" aria-hidden="true">
                  <b>Senior <mark>Backend Engineer</mark></b>
                  <small>Acme Robotics · Berlin · Why: backend engineer</small>
                </div>
              </div>
            </article>
            <article className="tile" data-reveal data-spot style={after(0.14)}>
              <p className="tile-k">One tap</p>
              <h3>A CV and a cover letter, made for the job</h3>
              <p>Built from your own CV, with the parts that fit that job first. Nothing is made up.</p>
              <div className="tile-demo">
                <div className="one-tap" aria-hidden="true">
                  <svg className="ring" viewBox="0 0 40 40">
                    <circle className="track" cx="20" cy="20" r="17" />
                    <circle className="fill" cx="20" cy="20" r="17" pathLength={1} />
                  </svg>
                  <span>Make my CV</span>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section id="how" className="section">
          <div className="head center" data-reveal>
            <p className="pill"><i />02 · How it works</p>
            <h2>Six steps, <span className="glow">always on.</span></h2>
            <p className="lp-lede">
              It <strong>collects</strong> jobs {often}, <strong>matches</strong> them to your words, and{" "}
              <strong>makes</strong> your documents when you ask.
            </p>
          </div>
          <div className="stack">
            {steps(often).map(([name, text], i) => (
              <article key={name} className="stack-card" style={{ "--i": i } as CSSProperties}>
                <div className="stack-shade" aria-hidden="true" />
                <div>
                  <span className="stack-num">0{i + 1} / 06</span>
                  <h3>{name}</h3>
                  <p>{text}</p>
                </div>
                {/* Its own spoke lit and turned to the top: the wheel a notch further on each card. */}
                <div className="stack-art" style={{ rotate: `${-i * 60}deg` }}>
                  <Wheel ticks={24} spokes={6} lit={i} />
                </div>
              </article>
            ))}
          </div>
        </section>

        <section id="live" className="section">
          <div className="head" data-reveal>
            <p className="pill"><i />03 · Your jobs</p>
            <h2>Every match says <span className="glow">why it is there.</span></h2>
            <p className="lp-lede">
              Your Jobs page lists every match, the jobs with the most of your words first, and says which words
              found each one. One tap makes the CV or the cover letter. You apply on the job's own site, then
              mark it as applied.
            </p>
          </div>
          <div className="live-grid">
            <Phone />
            <div data-reveal style={after(0.12)}>
              <p className="lp-lede">
                <strong>On your phone, if you like.</strong> Connect your own Telegram bot, or turn on browser
                notifications, and new matches arrive as they are found, each with a link straight to its CV.
              </p>
              <div className="alert-card" role="img" aria-label="A new job, as an alert on a phone">
                <div className="alert-app"><i><Telegram /></i>Jordan's job bot<span>now</span></div>
                <div className="alert-title">Python Engineer, Data Platform</div>
                <div className="alert-meta">Northwind · LinkedIn · 2h<br />📍 London · 🏠 Hybrid<br />Matches: python, data platform</div>
                <div className="alert-buttons"><span>Apply on LinkedIn</span><span>Make my CV</span></div>
              </div>
              <p className="try">Try it: press Make my CV on the card</p>
            </div>
          </div>
        </section>

        <section id="start" className="section">
          <div className="head center" data-reveal>
            <p className="pill"><i />04 · Get started</p>
            <h2>A few minutes to set up. <span className="glow">Then it searches while you sleep.</span></h2>
          </div>
          <div className="paths">
            <article className="tile path" data-reveal data-spot>
              <p className="tile-k">Step 1 · a few minutes</p>
              <h3>Set it up once</h3>
              <p>
                Create an account and upload your CV. We read it and suggest the words to search with; you check
                them and tap Looks good. That is all.
              </p>
              <ul>
                <li>Your CV (a cover letter and an "about you" note are optional)</li>
                <li>Three or more words to find jobs with, and any words to hide</li>
                <li>The countries you can work in, how you work, and your languages</li>
              </ul>
            </article>
            <article className="tile path" data-reveal data-spot style={after(0.08)}>
              <p className="tile-k">Step 2 · it runs itself</p>
              <h3>Then it works for you</h3>
              <p>
                New jobs are matched to your words as soon as their text is read. Each one is a tap away from a CV
                and a cover letter made for it.
              </p>
              <ul>
                <li>Every match says why it is there</li>
                <li>One tap makes a CV or a cover letter</li>
                <li>Alerts on your phone, if you want them</li>
              </ul>
            </article>
          </div>
          <div className="cta" data-reveal>
            <Wheel ticks={0} />
            <p>No waiting: your first matches are there as soon as you finish, from jobs already collected.</p>
            <a className="btn btn-primary btn-lg" href="#/signup">Create your account <Arrow /></a>
          </div>
          <p className="honest" data-reveal>
            Honest by design: there is no hidden score, and it never applies for you. Every job is shown because
            of words you chose, and every line of your CV comes from what you wrote. Alerts come by Telegram, by
            your browser's notifications, or in one email each morning.
          </p>
        </section>
      </main>

      <footer className="lp-foot">
        <div className="foot-halo" aria-hidden="true"><div><Wheel /></div></div>
        <p className="foot-name" aria-hidden="true">Mahoraga</p>
        <div className="foot-row">
          <span>The job-search radar · the wheel keeps turning</span>
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

/** The new jobs of each run in the last six hours, a bar a run: what was found, not a made-up shape. Not
 * drawn while nothing has been, which is a harvest that has stopped and not a quiet morning. */
function Found({ runs, minutes }: { runs: number[]; minutes: number }) {
  const total = runs.reduce((sum, run) => sum + run, 0);
  if (!total) return null;
  const most = Math.max(...runs);
  return (
    <figure className="found">
      <div className="bars" role="img"
           aria-label={`New jobs found in each ${minutes}-minute run of the last six hours: ${total.toLocaleString("en")} in all`}>
        {runs.map((run, i) => (
          <i key={i} style={{ "--h": Math.max(0.04, run / most), "--i": i } as CSSProperties} />
        ))}
      </div>
      <figcaption>
        <span>{total.toLocaleString("en")} new jobs in the last six hours</span>
        <span>Now</span>
      </figcaption>
    </figure>
  );
}

/** Four words to tap, each turning from a word that finds jobs into one that hides them and back. */
function WordDemo() {
  const [finds, setFinds] = useState([true, true, false, false]);
  return (
    <div className="tile-demo">
      <div className="word-demo">
        {["nurse", "backend engineer", "senior", "unpaid"].map((word, i) => (
          <button key={word} type="button" className={finds[i] ? "finds" : "hides"} aria-pressed={finds[i]}
                  onClick={() => setFinds(finds.map((each, k) => (k === i ? !each : each)))}>
            {word}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The Jobs page on a phone, as the app draws it: its bar, its two tabs, a job that works, and the tab bar
 * in pictures. */
function Phone() {
  return (
    <figure className="phone" data-reveal aria-label="The Jobs page, on a phone">
      <div className="phone-screen">
        <div className="phone-bar" aria-hidden="true">
          <span className="brand"><Mark />Mahoraga</span>
          <span className="phone-icons">
            <i><svg viewBox="0 0 24 24"><path d="M9.5 9.2a2.6 2.6 0 115 1c-.6 1-2.5 1.5-2.5 3M12 17h.01" /></svg></i>
            <i><svg viewBox="0 0 24 24"><path d="M6 16v-5a6 6 0 1112 0v5l1.5 2h-15zM10 20.5h4" /></svg></i>
          </span>
        </div>
        <div className="phone-body">
          <div className="tabs" aria-hidden="true"><span className="tab on">Your jobs</span><span className="tab">Add a job</span></div>
          <p className="kicker">Your jobs</p>
          <p className="phone-title">12 jobs match your words</p>
          <p className="small muted">From the last 24 hours · slower boards: the last 7 days</p>
          <Demo />
        </div>
        <div className="phone-tabs" aria-hidden="true">
          {PLACES.filter(([key]) => key !== "admin").map(([key, , icon], i) => (
            <span key={key} className={i === 0 ? "on" : undefined}><svg viewBox="0 0 24 24">{icon}</svg></span>
          ))}
        </div>
      </div>
    </figure>
  );
}

function Arrow() {
  return (
    <svg className="arrow" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Telegram's paper plane, in one colour. */
function Telegram() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M21.94 4.6 18.9 18.95c-.23 1.01-.83 1.26-1.68.78l-4.64-3.42-2.24 2.15c-.25.25-.46.46-.94.46l.33-4.72 8.6-7.77c.37-.33-.08-.52-.58-.19L7.12 12.93l-4.57-1.43c-.99-.31-1.01-.99.21-1.47L20.5 3.4c.83-.31 1.55.19 1.44 1.2z" />
    </svg>
  );
}
