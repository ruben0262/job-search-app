/** The Jobs page's card, working, on a made-up job: it writes a CV or a cover letter in front of you, says
 * where you would apply, asks before a Skip, and offers an Undo after "Mark as applied". The app's own
 * classes and pictures, so it looks as the real one does. Fictional, as every example on the landing page
 * is: never real pipeline output. */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { NEXT, STEPS } from "../pages/AppliedPage";
import { MORE_PICTURES, PICTURES } from "../pages/JobsPage";
import { MORE_ICON, PLACES, TABS } from "../places";
import { Prompt } from "../ui";
import { Mark } from "../wheel";

type Kind = "cv" | "cover";
type Line = [kind: "h" | "m" | "s" | "b" | "l" | "p", text: string];

// The landing page's examples are work anyone does (owner, 2026-11-14): the phone is a warehouse team
// leader's, the flow above it a nurse's. Every one was a software job before.
const TITLE = "Warehouse Team Leader";

// Jordan's CV, the parts that fit this job first. Nothing in the letter that the CV does not say.
const LINES: Record<Kind, Line[]> = {
  cv: [
    ["h", "Jordan Ellery"],
    ["m", "Team Leader · Berlin, Germany"],
    ["s", "Experience"],
    ["b", "Team Leader, Northwind · 2021 – now"],
    ["l", "Lead a shift of 14 pickers and loaders"],
    ["l", "Cut picking errors by a third in a year"],
    ["b", "Forklift Driver, Contoso · 2018 – 2021"],
    ["l", "Loaded 20 lorries a day"],
    ["s", "Languages"],
    ["l", "English (native), German (B2)"],
  ],
  cover: [
    ["h", "Jordan Ellery"],
    ["m", "To the Acme Logistics team"],
    ["p", "I run the kind of shift your depot needs. At Northwind I lead 14 pickers and loaders, and we cut picking errors by a third in a year."],
    ["p", "Getting every lorry out of your Berlin depot on time is that work at a larger scale, and it is the work I want to do next."],
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
  const [saved, setSaved] = useState(false);
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
      <article className={mark === "applied" ? "job-card gone" : "job-card gone skipped"}>
        <span className="gone-mark">{mark === "applied" ? PICTURES.applied : PICTURES.skip}</span>
        <p><strong>{mark === "applied" ? "Moved to Applied" : "Skipped"}:</strong> {TITLE}</p>
        <button type="button" className="secondary small undo" onClick={() => setMark(null)}>
          {MORE_PICTURES.undo}{mark === "applied" ? "Undo" : "Show it again"}
        </button>
      </article>
    );
  }

  const slot = (kind: Kind) => made.has(kind) && making !== kind ? (
    <span className="make">
      {/* Theirs to download once they have an account. */}
      <a className={kind === "cover" ? "button secondary" : "button"} href="/signup">
        {MORE_PICTURES.download}Download {NAME[kind]}
      </a>
      <button type="button" className="again" disabled={making !== null} onClick={() => make(kind)}>
        {MORE_PICTURES.again}Make it again
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
      <button type="button" className={saved ? "bookmark on" : "bookmark"} aria-pressed={saved}
              aria-label={saved ? "Remove bookmark" : "Bookmark this job"}
              onClick={() => setSaved(!saved)}>
        {PICTURES.bookmark}
      </button>
      <h3>{TITLE}</h3>
      <p className="job-meta">
        <strong className="company">Acme Logistics</strong>
        <span className="country">Germany</span>
        Berlin · Posted 2 hours ago
      </p>
      <p className="job-tags">
        <span className="tag match" style={{ "--fit": 9 } as CSSProperties}>Match 9/10</span>
        <span className="tag new">New</span>
        <span className="tag">€38–44k a year</span>
      </p>
      <p className="why">
        Your words: <span className="word green">warehouse</span> <span className="word green">team leader</span>
      </p>
      <div className="job-docs">
        {slot("cv")}
        {slot("cover")}
      </div>
      {shown ? <Paper lines={LINES[shown]} written={written} /> : null}
      <div className="job-bar">
        {/* A made-up job has nowhere to open, so it says where a real one does. */}
        <button type="button" className="bar-action apply" onClick={() => setTold(true)}>
          Apply on LinkedIn{PICTURES.apply}
        </button>
        <button type="button" className="bar-action" onClick={() => setMark("applied")}>
          {PICTURES.applied}Mark as applied
        </button>
        <button type="button" className="bar-action skip" onClick={() => setAsking(true)}>
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
 * through the site by itself (owner, 2026-11-12), as somebody uses it: it makes a CV for a job, bookmarks it,
 * marks it applied, then on Applied hears back and the job moves along its track, then shows My search, a touch
 * where each tap lands, and round again (owner, 2026-11-17: "more animations to it ... so users understand the
 * app more"). The first touch of the visitor's own ends the tour, and the phone is theirs: the card works, the
 * questions answer and the tabs change the page. Still for reduced motion. */
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
    const ready = () => (visible ? Promise.resolve() : new Promise<void>((done) => {
      wake = done;
    }));
    // A touch where the tap lands, the phone's page first brought round to it if it is out of sight (the page
    // alone, never the window), and, for a button the tour presses, the press.
    const tap = async (selector: string, press = false) => {
      await ready();
      const target = phone.querySelector<HTMLElement>(selector);
      if (stopped || !target) return;
      const page = body.current;
      if (page) {
        const shows = page.getBoundingClientRect();
        const box = target.getBoundingClientRect();
        if (box.top < shows.top + 40 || box.bottom > shows.bottom - 110) {
          page.scrollTo({ top: page.scrollTop + box.top - shows.top - 140, behavior: still() ? "auto" : "smooth" });
          await pause(450, timers);
        }
      }
      if (stopped) return;
      const outer = phone.getBoundingClientRect();
      const box = target.getBoundingClientRect();
      setTouch((last) => ({ x: box.left - outer.left + box.width / 2, y: box.top - outer.top + box.height / 2,
                            n: (last?.n ?? 0) + 1 }));
      await pause(350, timers);
      if (press && !stopped) target.click();
    };
    const go = (to: Screen) => {
      setPage(to);
      body.current?.scrollTo({ top: 0 });
    };
    void (async () => {
      while (!stopped) {
        await ready();
        await pause(1500, timers);
        await tap(".demo-card .make button");
        if (stopped) return;
        setAuto((n) => n + 1);
        await pause(700, timers);
        const paper = phone.querySelector<HTMLElement>(".paper");
        if (paper && body.current) body.current.scrollTo({ top: paper.offsetTop - 120, behavior: "smooth" });
        await pause(2800, timers);
        // Kept for later, then applied to on its own site and marked so: the card says it moved to Applied.
        await tap(".demo-card .bookmark", true);
        await pause(900, timers);
        await tap(".demo-card .job-bar .bar-action:not(.apply):not(.skip)", true);
        await pause(1600, timers);
        if (stopped) return;
        // My search is under More, as in the app.
        for (const to of ["applied", "more", "jobs"] as const) {
          await tap(`.phone-tabs [data-tab="${to}"]`);
          if (stopped) return;
          go(to === "more" ? "words" : to);
          if (to === "jobs") {
            setRound((n) => n + 1);
            continue;
          }
          // Applied: heard back, and the first application moves along its track.
          if (to === "applied") {
            await pause(1400, timers);
            await tap(".progress-answers button", true);
          }
          await pause(3200, timers);
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
          {/* The bell alone, as a phone's bar has it: help and the theme are in More. */}
          <span className="phone-icons">
            <i><svg viewBox="0 0 24 24"><path d="M6 16v-5a6 6 0 1112 0v5l1.5 2h-15zM10 20.5h4" /></svg></i>
          </span>
        </div>
        <div className="phone-body" ref={body}>
          <div key={page} className="phone-page">
            {page === "applied" ? <AppliedScreen /> : page === "words" ? <WordsScreen /> : (
              <>
                <div className="tabs" aria-hidden="true">
                  <span className="tab on">Your jobs</span><span className="tab">Bookmarks</span>
                  <span className="tab">Add a job</span>
                </div>
                <p className="phone-title">12 jobs match your words</p>
                {/* The Jobs page's search, how far back and its filters, as the app draws them on a phone. */}
                <div className="finder" aria-hidden="true">
                  <input type="search" placeholder="Search your jobs" readOnly tabIndex={-1} />
                  <select tabIndex={-1} defaultValue="24"><option value="24">Last 24 hours</option></select>
                  <button type="button" className="secondary finder-open" tabIndex={-1}>
                    {MORE_PICTURES.filters}Filters
                  </button>
                </div>
                <Demo key={round} auto={auto} />
              </>
            )}
          </div>
        </div>
        {/* The app's tab bar, as a phone draws it (`places.tsx`): the tour taps it, and so can a visitor, for the
            three pages the phone has. My search is in More, so More opens it and is lit on it. */}
        <div className="phone-tabs" aria-hidden="true">
          {[...PLACES.filter(([key]) => TABS.includes(key)), ["more", "More", MORE_ICON, "More"] as const].map(
            ([key, , icon, short]) => (
              <span key={key} data-tab={key} className={(key === "more" ? page === "words" : key === page) ? "on" : undefined}
                    onClick={key === "jobs" || key === "applied" ? () => setPage(key)
                      : key === "more" ? () => setPage("words") : undefined}>
                <svg viewBox="0 0 24 24">{icon}</svg>
                {short}
              </span>
            ))}
        </div>
        {touch ? <span key={touch.n} className="phone-touch" style={{ left: touch.x, top: touch.y }} /> : null}
      </div>
    </figure>
  );
}

/** Applied, as the app draws it: each application on the one track it moves along, its question answered here
 * as it is there. Made-up applications. */
const APPLICATIONS: [title: string, company: string, reached: number][] = [
  [TITLE, "Acme Logistics", 0],
  ["Logistics Coordinator", "Northwind", 1],
  ["Shift Supervisor", "Contoso", 2],
];

function AppliedScreen() {
  return (
    <>
      <p className="phone-title">You applied to 3 jobs</p>
      {APPLICATIONS.map(([title, company, reached]) => (
        <Application key={title} title={title} company={company} from={reached} />
      ))}
    </>
  );
}

/** One application: its track, and the question that moves it on, with Undo for a tap made by mistake. */
function Application({ title, company, from }: { title: string; company: string; from: number }) {
  // Each answer as [how far it had got, whether it had ended], so Undo puts back the one before.
  const [answers, setAnswers] = useState<[number, boolean][]>([]);
  const [reached, setReached] = useState(from);
  const [ended, setEnded] = useState(false);
  const stage = STEPS[reached]?.[0] ?? "applied";
  const next = ended ? undefined : NEXT[stage];
  const answer = (forward: boolean) => {
    setAnswers([...answers, [reached, ended]]);
    if (forward) setReached(reached + 1);
    else setEnded(true);
  };
  const undo = () => {
    const [was, end] = answers.at(-1) ?? [from, false];
    setReached(was);
    setEnded(end);
    setAnswers(answers.slice(0, -1));
  };
  return (
    <article className="job-card">
      <h3>{title}</h3>
      <p className="job-meta"><strong className="company">{company}</strong>Applied this week</p>
      <div className={ended ? "progress ended" : "progress"}>
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
        <div className="progress-next">
          {ended ? <p className="progress-note no">Not this time. On to the next one.</p>
            : stage === "hired" ? <p className="progress-note yes">You got the job. Congratulations!</p>
              : next ? (
                <>
                  <p className="progress-ask">{next[0]}</p>
                  <div className="progress-answers">
                    <button type="button" className="small" onClick={() => answer(true)}>{next[2]}</button>
                    <button type="button" className="secondary small" onClick={() => answer(false)}>Not this time</button>
                  </div>
                </>
              ) : null}
          {answers.length ? (
            <button type="button" className="secondary small undo" onClick={undo}>{MORE_PICTURES.undo}Undo</button>
          ) : null}
        </div>
      </div>
      <div className="job-bar">
        <span className="bar-action apply">Open the job{PICTURES.apply}</span>
      </div>
    </article>
  );
}

/** My search, as the app draws it: the words that find jobs, the words that hide them, and where. */
function WordsScreen() {
  const rows: [key: string, label: string, tone: string, words: string[]][] = [
    ["green", "Green words find jobs", "green", ["warehouse", "team leader", "logistics"]],
    ["red", "Red words hide them", "red", ["agency", "night shift"]],
    ["places", "Countries", "plain", ["Germany", "Netherlands"]],
    ["ways", "Ways of working", "plain", ["On-site", "Hybrid"]],
  ];
  return (
    <>
      <p className="phone-title">The jobs you want</p>
      {rows.map(([key, label, tone, words]) => (
        <div key={key} className="phone-words">
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
  ["Warehouse Team Leader", "Northwind · LinkedIn", "📍 Rotterdam · 🏢 On-site", "warehouse, team leader"],
  ["Logistics Coordinator", "Contoso · Greenhouse", "📍 Amsterdam · 🏠 Hybrid", "logistics"],
  ["Shift Supervisor, Distribution", "Fabrikam · Personio", "📍 Berlin · 🏢 On-site", "warehouse"],
  ["Transport Planner", "Tailspin · Workable", "📍 Dublin · 🌍 Remote", "logistics"],
  ["Stock Control Team Leader", "Litware · Lever", "📍 Lisbon · 🏢 On-site", "team leader"],
];
const SHOWN = 3;
const EVERY_MS = 2800;

export function Alerts() {
  const box = useRef<HTMLDivElement>(null);
  // How many have arrived: the newest on top, the two before it under it, and the one before those folding away.
  const [arrived, setArrived] = useState(() => (still() ? SHOWN : 1));
  // The room of SHOWN alerts, kept from the first one: they arrive, move down and fold away inside it, and
  // nothing under it moves (owner, 2026-11-16: the reviews below went up and down with them, by up to 430 px
  // on a phone). Measured on the cards as drawn, the tallest, again when the width or the fonts change.
  const [room, setRoom] = useState<number>();
  useLayoutEffect(() => {
    const here = box.current;
    if (!here) return;
    const measure = () => {
      const cards = [...here.querySelectorAll<HTMLElement>(".alert-card")];
      const fold = here.querySelector<HTMLElement>(".alert-fold");
      if (!cards.length || !fold) return;
      const slot = Math.max(...cards.map((card) => card.offsetHeight)) + parseFloat(getComputedStyle(fold).paddingBottom);
      setRoom((was) => Math.max(was ?? 0, Math.ceil(slot * SHOWN)));
    };
    measure();
    void document.fonts?.ready.then(measure);
    const watch = new ResizeObserver(() => {
      setRoom(undefined);
      measure();
    });
    watch.observe(here);
    return () => watch.disconnect();
  }, []);
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
    <div className="alerts" ref={box} role="img" aria-label="New jobs arriving as alerts on a phone"
         style={room ? { height: room } : undefined}>
      {cards.map((n, i) => {
        const [title, from, place, words] = ALERTS[n % ALERTS.length]!;
        return (
          <div key={n} className={i === SHOWN ? "alert-slot leaving" : "alert-slot"}>
            <div className="alert-fold">
              <div className="alert-card">
                <div className="alert-app"><i><Mark /></i>Mahoraga<span>{i ? `${i * 3} min ago` : "now"}</span></div>
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

