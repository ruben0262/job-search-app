/** The Jobs page's card, working, on a made-up job: it writes a CV or a cover letter in front of you, says
 * where you would apply, asks before a Skip, and offers an Undo after "Mark as applied". The app's own
 * classes and pictures, so it looks as the real one does. Fictional, as every example on the landing page
 * is: never real pipeline output. */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { STEPS } from "../pages/AppliedPage";
import { PICTURES } from "../pages/JobsPage";
import { PLACES } from "../places";
import { Prompt } from "../ui";
import { Mark } from "../wheel";

type Kind = "cv" | "cover";
type Line = [kind: "h" | "m" | "s" | "b" | "l" | "p", text: string];

const TITLE = "Senior Backend Engineer";

// Jordan's CV, the parts that fit this job first. Nothing in the letter that the CV does not say.
const LINES: Record<Kind, Line[]> = {
  cv: [
    ["h", "Jordan Ellery"],
    ["m", "Backend Engineer · Berlin, Germany"],
    ["s", "Experience"],
    ["b", "Backend Engineer, Northwind · 2021 – now"],
    ["l", "Built the billing service in Python and Postgres"],
    ["l", "Cut checkout latency to 40 ms at p99"],
    ["b", "Software Engineer, Contoso · 2018 – 2021"],
    ["l", "Moved the payments API to Go"],
    ["s", "Languages"],
    ["l", "English (native), German (B2)"],
  ],
  cover: [
    ["h", "Jordan Ellery"],
    ["m", "To the Acme Robotics team"],
    ["p", "I build the kind of backend a fleet reports to. At Northwind I built the billing service in Python and Postgres, and cut checkout latency to 40 ms at p99."],
    ["p", "Moving telemetry from the warehouse floor to the cloud is that work at a larger scale, and it is the work I want to do next."],
    ["p", "Kind regards, Jordan"],
  ],
};

const NAME: Record<Kind, string> = { cv: "CV", cover: "cover letter" };
const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** `auto`: the phone's tour taps "Make my CV" each time it goes up. */
export function Demo({ auto = 0 }: { auto?: number }) {
  const [shown, setShown] = useState<Kind | null>(null);
  const [making, setMaking] = useState<Kind | null>(null);
  const [made, setMade] = useState<Set<Kind>>(new Set());
  const [written, setWritten] = useState(0);
  const [mark, setMark] = useState<"applied" | "skipped" | null>(null);
  const [asking, setAsking] = useState(false);
  const [told, setTold] = useState(false);
  // Said at the card's foot, which the phone may have scrolled out of sight: brought into it.
  const note = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (told) note.current?.scrollIntoView({ block: "nearest", behavior: still() ? "auto" : "smooth" });
  }, [told]);

  // A few letters a frame, the way a document streams while it is made.
  useEffect(() => {
    if (!making) return;
    const total = LINES[making].reduce((sum, [, text]) => sum + text.length, 0);
    let count = still() ? total : 0;
    let frame = 0;
    const step = () => {
      count = Math.min(total, count + 4);
      setWritten(count);
      if (count < total) {
        frame = requestAnimationFrame(step);
        return;
      }
      setMade((now) => new Set(now).add(making));
      setMaking(null);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [making]);

  const make = (kind: Kind) => {
    setShown(kind);
    setWritten(0);
    setMaking(kind);
  };
  // A tap of the tour's is `auto` going up, not the count a card is made with: a card made again, by a tab
  // or by a new round, waits for its own tap.
  const tapped = useRef(auto);
  useEffect(() => {
    if (auto === tapped.current) return;
    tapped.current = auto;
    make("cv");
  }, [auto]);

  if (mark) {
    return (
      <article className="job-card gone">
        <span className="muted small">{mark === "applied" ? "Moved to Applied" : "Skipped"}: {TITLE}</span>
        <button type="button" className="linklike" onClick={() => setMark(null)}>
          {mark === "applied" ? "Undo" : "Show it again"}
        </button>
      </article>
    );
  }

  const slot = (kind: Kind) => made.has(kind) && making !== kind ? (
    <span className="make">
      {/* Theirs to download once they have an account. */}
      <a className="button" href="#/signup">Download your {NAME[kind]}</a>
      <button type="button" className="linklike small" disabled={making !== null} onClick={() => make(kind)}>
        Make it again
      </button>
    </span>
  ) : (
    <span className="make">
      <button type="button" disabled={making !== null} onClick={() => make(kind)}
              className={[making === kind && "making", kind === "cover" && "secondary"].filter(Boolean).join(" ")}>
        {making === kind ? `Making your ${NAME[kind]}…` : `Make my ${NAME[kind]}`}
      </button>
    </span>
  );

  return (
    <article className={["job-card demo-card", made.has("cv") && "made-cv", made.has("cover") && "made-cover"]
      .filter(Boolean).join(" ")}>
      <h3>{TITLE}</h3>
      <p className="made-badges">
        <span className="made-badge new">New</span>
        {made.has("cv") ? <span className="made-badge cv">CV made</span> : null}
        {made.has("cover") ? <span className="made-badge cover">Cover letter made</span> : null}
      </p>
      <p className="job-meta">
        <strong className="company">Acme Robotics</strong>
        <span className="country">Germany</span>
        Berlin · Posted 2 hours ago
      </p>
      <p className="job-meta">€70–85k a year</p>
      <p className="summary">
        Build the services that move our robots' telemetry from the warehouse floor to the cloud. Python,
        Postgres, Kafka.
      </p>
      <p className="why">
        Why: <span className="word green">backend engineer</span> <span className="word green">python</span>
      </p>
      <div className="job-actions">
        {slot("cv")}
        {slot("cover")}
      </div>
      {shown ? <Paper lines={LINES[shown]} written={written} /> : null}
      <div className="job-actions marks">
        {/* A made-up job has nowhere to open, so it says where a real one does. */}
        <button type="button" className="secondary small" onClick={() => setTold(true)}>
          Apply on LinkedIn{PICTURES.apply}
        </button>
        <button type="button" className="secondary small" onClick={() => setMark("applied")}>
          {PICTURES.applied}Mark as applied
        </button>
        <button type="button" className="secondary small skip" onClick={() => setAsking(true)}>
          {PICTURES.skip}Skip
        </button>
      </div>
      {told ? (
        <p ref={note} className="small muted">
          On a real job, this opens it on LinkedIn, and you apply there with the CV you made here.
        </p>
      ) : null}
      {asking ? (
        <Prompt title="Skip this job?" onClose={() => setAsking(false)}>
          <p className="small muted"><strong>{TITLE}</strong> leaves your list and is not shown to you again.</p>
          <div className="row">
            <button type="button" autoFocus onClick={() => {
              setAsking(false);
              setMark("skipped");
            }}>
              Skip it
            </button>
            <button type="button" className="secondary" onClick={() => setAsking(false)}>Keep it</button>
          </div>
        </Prompt>
      ) : null}
    </article>
  );
}

/** The document on its page, written up to `written` letters. The rest is laid out already, unseen, so
 * nothing under it moves while it is written. */
function Paper({ lines, written }: { lines: Line[]; written: number }) {
  const total = lines.reduce((sum, [, text]) => sum + text.length, 0);
  let left = written;
  return (
    <div className="paper" aria-hidden="true">
      <div className="paper-bar"><span style={{ transform: `scaleX(${written / total})` }} /></div>
      {lines.map(([kind, text], index) => {
        const done = Math.max(0, Math.min(left, text.length));
        left -= text.length;
        return (
          <p key={index} className={done > 0 && done < text.length ? `paper-${kind} typing` : `paper-${kind}`}>
            <span className="paper-done">{text.slice(0, done)}</span>
            <span className="paper-rest">{text.slice(done)}</span>
          </p>
        );
      })}
    </div>
  );
}

type Screen = "jobs" | "applied" | "words";

/** Wait `ms`, unless the tour is stopped: then never, and whatever waits is let go with it. */
function pause(ms: number, timers: number[]): Promise<void> {
  return new Promise((done) => {
    timers.push(window.setTimeout(done, ms));
  });
}

/** The app on a phone, as it draws itself: its bar, a page, and the tab bar. While it is on the screen it goes
 * through the site by itself (owner, 2026-11-12): it makes a CV on the Jobs page, then shows Applied and My search,
 * a touch where each tap lands, and round again. The first touch of the visitor's own ends the tour, and the phone
 * is theirs: the card works and the tabs change the page. Still for reduced motion. */
export function Phone() {
  const screen = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState<Screen>("jobs");
  // A new card each time round, and its CV made when `auto` goes up.
  const [round, setRound] = useState(0);
  const [auto, setAuto] = useState(0);
  const [touch, setTouch] = useState<{ x: number; y: number; n: number } | null>(null);
  const [touring, setTouring] = useState(() => !still());

  useEffect(() => {
    const phone = screen.current;
    if (!touring || !phone) return;
    const timers: number[] = [];
    let stopped = false;
    let visible = false;
    let wake: (() => void) | null = null;
    const seen = new IntersectionObserver(([entry]) => {
      visible = Boolean(entry?.isIntersecting);
      if (visible) wake?.();
    }, { threshold: 0.5 });
    seen.observe(phone);
    // Each step waits until the phone is in sight: nothing moves where nobody is looking.
    const inSight = () => (visible ? Promise.resolve() : new Promise<void>((done) => {
      wake = done;
    }));
    const tap = async (selector: string) => {
      await inSight();
      const target = phone.querySelector<HTMLElement>(selector);
      if (stopped || !target) return;
      const outer = phone.getBoundingClientRect();
      const box = target.getBoundingClientRect();
      setTouch((last) => ({ x: box.left - outer.left + box.width / 2, y: box.top - outer.top + box.height / 2,
                            n: (last?.n ?? 0) + 1 }));
      await pause(350, timers);
    };
    const go = (to: Screen) => {
      setPage(to);
      body.current?.scrollTo({ top: 0 });
    };
    void (async () => {
      while (!stopped) {
        await inSight();
        await pause(1500, timers);
        await tap(".demo-card .make button");
        if (stopped) return;
        setAuto((n) => n + 1);
        await pause(700, timers);
        const paper = phone.querySelector<HTMLElement>(".paper");
        if (paper && body.current) body.current.scrollTo({ top: paper.offsetTop - 120, behavior: "smooth" });
        await pause(2800, timers);
        for (const to of ["applied", "words", "jobs"] as const) {
          await tap(`.phone-tabs [data-tab="${to}"]`);
          if (stopped) return;
          go(to);
          if (to === "jobs") setRound((n) => n + 1);
          else await pause(3200, timers);
        }
      }
    })();
    const quit = () => {
      stopped = true;
      setTouring(false);
      setTouch(null);
    };
    phone.addEventListener("pointerdown", quit);
    phone.addEventListener("focusin", quit);
    return () => {
      stopped = true;
      timers.forEach(clearTimeout);
      seen.disconnect();
      phone.removeEventListener("pointerdown", quit);
      phone.removeEventListener("focusin", quit);
    };
  }, [touring]);

  return (
    <figure className="phone" data-reveal aria-label="The Jobs page, on a phone">
      <div className="phone-screen" ref={screen}>
        <div className="phone-bar" aria-hidden="true">
          <span className="brand"><Mark />Mahoraga</span>
          <span className="phone-icons">
            <i><svg viewBox="0 0 24 24"><path d="M9.5 9.2a2.6 2.6 0 115 1c-.6 1-2.5 1.5-2.5 3M12 17h.01" /></svg></i>
            <i><svg viewBox="0 0 24 24"><path d="M6 16v-5a6 6 0 1112 0v5l1.5 2h-15zM10 20.5h4" /></svg></i>
          </span>
        </div>
        <div className="phone-body" ref={body}>
          <div key={page} className="phone-page">
            {page === "applied" ? <AppliedScreen /> : page === "words" ? <WordsScreen /> : (
              <>
                <div className="tabs" aria-hidden="true">
                  <span className="tab on">Your jobs</span><span className="tab">Add a job</span>
                </div>
                <p className="phone-title">12 jobs match your words</p>
                <Demo key={round} auto={auto} />
              </>
            )}
          </div>
        </div>
        {/* The app's tab bar: the tour taps it, and so can a visitor, for the three pages the phone has. */}
        <div className="phone-tabs" aria-hidden="true">
          {PLACES.filter(([key]) => key !== "admin").map(([key, , , short]) => (
            <span key={key} data-tab={key} className={key === page ? "on" : undefined}
                  onClick={key === "jobs" || key === "applied" || key === "words" ? () => setPage(key) : undefined}>
              {short}
            </span>
          ))}
        </div>
        {touch ? <span key={touch.n} className="phone-touch" style={{ left: touch.x, top: touch.y }} /> : null}
      </div>
    </figure>
  );
}

/** Applied, as the app draws it: each application on the one track it moves along. Made-up applications. */
function AppliedScreen() {
  const applications: [title: string, company: string, reached: number][] = [
    ["Senior Backend Engineer", "Acme Robotics", 1],
    ["Data Engineer, Platform", "Northwind", 2],
    ["Python Developer", "Contoso", 0],
  ];
  return (
    <>
      <p className="phone-title">3 applications</p>
      {applications.map(([title, company, reached]) => (
        <article key={title} className="job-card">
          <h3>{title}</h3>
          <p className="job-meta"><strong className="company">{company}</strong></p>
          <div className="progress">
            <ol style={{ "--done": reached / (STEPS.length - 1) } as CSSProperties}>
              {STEPS.map(([key, label], index) => (
                <li key={key} className={index <= reached ? "done" : undefined}>
                  <span className="dot">
                    {index <= reached ? <svg viewBox="0 0 16 16"><path d="M3.5 8.4l2.9 2.9 6-6.3" /></svg> : null}
                  </span>
                  {label}
                </li>
              ))}
            </ol>
          </div>
        </article>
      ))}
    </>
  );
}

/** My search, as the app draws it: the words that find jobs, the words that hide them, and where. */
function WordsScreen() {
  const rows: [label: string, tone: string, words: string[]][] = [
    ["Green words find jobs", "green", ["backend engineer", "python", "data platform"]],
    ["Red words hide them", "red", ["php", "sales"]],
    ["Countries", "plain", ["Germany", "Netherlands"]],
    ["Ways of working", "plain", ["Remote", "Hybrid"]],
  ];
  return (
    <>
      <p className="phone-title">The jobs you want</p>
      {rows.map(([label, tone, words]) => (
        <div key={label} className="phone-words">
          <p className="small muted">{label}</p>
          <p>{words.map((word) => <span key={word} className={`word ${tone}`}>{word}</span>)}</p>
        </div>
      ))}
    </>
  );
}

/** New jobs as alerts on a phone, as `services/notifier/card.py` words them: one arrives at the top every few
 * seconds while they are in sight, and the older ones move down (owner, 2026-11-12). Made-up jobs. */
const ALERTS: [title: string, from: string, place: string, words: string][] = [
  ["Python Engineer, Data Platform", "Northwind · LinkedIn", "📍 London · 🏠 Hybrid", "python, data platform"],
  ["Backend Developer (Go)", "Contoso · Greenhouse", "📍 Amsterdam · 🌍 Remote", "backend, go"],
  ["Senior Software Engineer", "Fabrikam · Personio", "📍 Berlin · 🏢 On-site", "software engineer"],
  ["Data Engineer", "Tailspin · Workable", "📍 Dublin · 🏠 Hybrid", "data engineer, sql"],
  ["Platform Engineer", "Litware · Lever", "📍 Lisbon · 🌍 Remote", "platform, kubernetes"],
];
const SHOWN = 3;
const EVERY_MS = 2800;

export function Alerts() {
  const box = useRef<HTMLDivElement>(null);
  // How many have arrived: the newest on top, the two before it under it, and the one before those folding away.
  const [arrived, setArrived] = useState(() => (still() ? SHOWN : 1));
  useEffect(() => {
    if (still() || !box.current) return;
    let visible = false;
    const seen = new IntersectionObserver(([entry]) => {
      visible = Boolean(entry?.isIntersecting);
    }, { threshold: 0.3 });
    seen.observe(box.current);
    const timer = window.setInterval(() => {
      if (visible) setArrived((n) => n + 1);
    }, EVERY_MS);
    return () => {
      clearInterval(timer);
      seen.disconnect();
    };
  }, []);
  // Newest first; one more than shown, folding away.
  const cards = Array.from({ length: Math.min(arrived, SHOWN + 1) }, (_, i) => arrived - 1 - i);
  return (
    <div className="alerts" ref={box} role="img" aria-label="New jobs arriving as alerts on a phone">
      {cards.map((n, i) => {
        const [title, from, place, words] = ALERTS[n % ALERTS.length]!;
        return (
          <div key={n} className={i === SHOWN ? "alert-slot leaving" : "alert-slot"}>
            <div className="alert-fold">
              <div className="alert-card">
                <div className="alert-app"><i><Telegram /></i>Jordan's job bot<span>{i ? `${i * 3} min ago` : "now"}</span></div>
                <div className="alert-title">{title}</div>
                <div className="alert-meta">{from}<br />{place}<br />Matches: {words}</div>
                <div className="alert-buttons"><span>Apply on LinkedIn</span><span>Make my CV</span></div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
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
