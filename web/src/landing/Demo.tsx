/** The Jobs page's card, working, on a made-up job: it writes a CV or a cover letter in front of you, says
 * where you would apply, asks before a Skip, and offers an Undo after "Mark as applied". The app's own
 * classes and pictures, so it looks as the real one does. Fictional, as every example on the landing page
 * is: never real pipeline output. */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { NEXT, STEPS } from "../pages/AppliedPage";
import { MORE_PICTURES, PICTURES } from "../pages/JobsPage";
import { PLACES } from "../places";
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
    <span className="make" data-explain={kind}>
      {/* Theirs to download once they have an account. */}
      <a className={kind === "cover" ? "button secondary" : "button"} href="#/signup">
        {MORE_PICTURES.download}Download {NAME[kind]}
      </a>
      <button type="button" className="again" disabled={making !== null} onClick={() => make(kind)}>
        {MORE_PICTURES.again}Make it again
      </button>
    </span>
  ) : (
    <span className="make" data-explain={kind}>
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
              aria-label={saved ? "Remove bookmark" : "Bookmark this job"} data-explain="bookmark"
              onClick={() => setSaved(!saved)}>
        {PICTURES.bookmark}
      </button>
      <h3>{TITLE}</h3>
      <p className="job-meta" data-explain="where">
        <strong className="company">Acme Logistics</strong>
        <span className="country">Germany</span>
        Berlin · Posted 2 hours ago
      </p>
      <p className="job-tags">
        <span className="tag match" style={{ "--fit": 9 } as CSSProperties} data-explain="match">Match 9/10</span>
        <span className="tag new" data-explain="new">New</span>
        <span className="tag" data-explain="pay">€38–44k a year</span>
      </p>
      <p className="summary" data-explain="summary">
        Lead a shift of 12 at our Berlin depot: picking, loading and every lorry out on time. Forklift
        licence needed.
      </p>
      <p className="why" data-explain="words">
        Your words: <span className="word green">warehouse</span> <span className="word green">team leader</span>
      </p>
      <div className="job-docs">
        {slot("cv")}
        {slot("cover")}
      </div>
      {shown ? <Paper lines={LINES[shown]} written={written} /> : null}
      <div className="job-bar">
        {/* A made-up job has nowhere to open, so it says where a real one does. */}
        <button type="button" className="bar-action apply" data-explain="apply" onClick={() => setTold(true)}>
          Apply on LinkedIn{PICTURES.apply}
        </button>
        <button type="button" className="bar-action" data-explain="applied" onClick={() => setMark("applied")}>
          {PICTURES.applied}Mark as applied
        </button>
        <button type="button" className="bar-action skip" data-explain="skip" onClick={() => setAsking(true)}>
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

/** What each part of the phone is for, said beside it when it is pointed at, or under it when it is tapped
 * (owner, 2026-11-17: "picks apart the things the user hovers ... what that section actually does in a short
 * description"). True of the app itself, in the words it uses. */
const EXPLAIN: Record<string, [title: string, text: string]> = {
  tabs: ["Your jobs, Bookmarks, Add a job",
         "Your list, the jobs you saved for later, and any job you found elsewhere, added by its link."],
  count: ["Your list", "Every job from every site we read that has your words, in one list."],
  finder: ["Search and filters",
           "Search your list, pick how far back to look, and open every other filter from one button."],
  bookmark: ["Bookmark", "Save a job for later. It waits for you under Bookmarks."],
  where: ["Who, where, when", "The company, the place, and how long ago the job was posted."],
  match: ["Match score", "How well the job fits your CV, out of 10. Show only the 7s and up, in one tap."],
  new: ["New", "Found since you last looked."],
  pay: ["Pay", "What the job pays, when it says."],
  summary: ["The job", "The start of the job's own text. Tap it to read all of it."],
  words: ["Your words", "The words from your search that this job has: why it is on your list."],
  cv: ["Make my CV",
       "One tap writes a CV for this job with your own AI account, then checks it the way an ATS reads it."],
  cover: ["Make my cover letter", "A cover letter for this job in one tap, checked the same way."],
  apply: ["Apply on the job's site",
          "Opens the job where it was posted. You apply there, with the CV you made here."],
  applied: ["Mark as applied", "Moves the job to Applied, where you follow it all the way to the offer."],
  skip: ["Skip", "Not for you? It leaves your list for good, once you say so."],
  track: ["Where it stands", "Every application on one track: applied, interview, offer, the job."],
  ask: ["Heard back?", "One tap moves it on, and Undo puts it back."],
  open: ["Open the job", "Back to the job's own page, whenever you need it."],
  green: ["Green words", "Words that find jobs for you."],
  red: ["Red words", "Words that hide jobs you don't want: agencies, night shifts."],
  places: ["Countries", "Where you want to work."],
  ways: ["Ways of working", "On-site, hybrid or remote."],
  bar: ["The whole app", "Jobs, Applied, your search, your documents and your settings: five tabs."],
};

/** A part of the phone pointed at or tapped: what it is, and its box on the phone, inside what the screen
 * shows. */
type Part = { key: string; top: number; left: number; width: number; height: number };

/** Wait `ms`, unless the tour is stopped: then never, and whatever waits is let go with it. */
function pause(ms: number, timers: number[]): Promise<void> {
  return new Promise((done) => {
    timers.push(window.setTimeout(done, ms));
  });
}

/** The app on a phone, as it draws itself: its bar, a page, and the tab bar. While it is on the screen it goes
 * through the site by itself (owner, 2026-11-12): it makes a CV on the Jobs page, then shows Applied, answers
 * its question, and My search, a touch where each tap lands, and round again. Pointed at, it waits, and the part
 * under the pointer is ringed and said beside the phone (owner, 2026-11-17); tapped, the part is said under it.
 * The first touch of the visitor's own ends the tour, and the phone is theirs: the card works, the questions
 * answer and the tabs change the page. Still for reduced motion. */
export function Phone() {
  const figure = useRef<HTMLElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState<Screen>("jobs");
  // A new card each time round, and its CV made when `auto` goes up.
  const [round, setRound] = useState(0);
  const [auto, setAuto] = useState(0);
  const [touch, setTouch] = useState<{ x: number; y: number; n: number } | null>(null);
  const [touring, setTouring] = useState(() => !still());
  const [part, setPart] = useState<Part | null>(null);
  const pointed = useRef<HTMLElement | null>(null);
  // The part under the pointer or the finger, kept until another: a move over the same part changes nothing.
  const point = (target: EventTarget | null) => {
    const found = target instanceof Element ? target.closest<HTMLElement>("[data-explain]") : null;
    if (found === pointed.current) return;
    pointed.current = found;
    measure(found);
  };
  // Where the part is now: again as the phone's page scrolls under the pointer, so the ring stays on it.
  const measure = (found: HTMLElement | null) => {
    const outer = figure.current?.getBoundingClientRect();
    const shows = body.current?.getBoundingClientRect();
    if (!found?.dataset.explain || !outer || !shows) {
      setPart(null);
      return;
    }
    const box = found.getBoundingClientRect();
    // Ringed where the screen shows it: a part half scrolled away is ringed as far as it is seen.
    const top = Math.max(box.top, found.closest(".phone-tabs") ? box.top : shows.top);
    const bottom = Math.min(box.bottom, found.closest(".phone-tabs") ? box.bottom : shows.bottom);
    setPart({ key: found.dataset.explain, top: top - outer.top, left: box.left - outer.left, width: box.width,
              height: Math.max(0, bottom - top) });
  };
  const forget = () => {
    pointed.current = null;
    setPart(null);
  };

  useEffect(() => {
    const phone = screen.current;
    if (!touring || !phone) return;
    const timers: number[] = [];
    let stopped = false;
    let visible = false;
    // Pointed at: the tour waits, so the part being read stays where it is (owner, 2026-11-17).
    let pointedAt = false;
    let wake: (() => void) | null = null;
    const free = () => visible && !pointedAt;
    const seen = new IntersectionObserver(([entry]) => {
      visible = Boolean(entry?.isIntersecting);
      if (free()) wake?.();
    }, { threshold: 0.5 });
    seen.observe(phone);
    // Each step waits until the phone is in sight and nobody is pointing at it: nothing moves where nobody is
    // looking, or under somebody reading.
    const ready = () => (free() ? Promise.resolve() : new Promise<void>((done) => {
      wake = done;
    }));
    // A touch where the tap lands, and, for a button the tour presses, the press.
    const tap = async (selector: string, press = false) => {
      await ready();
      const target = phone.querySelector<HTMLElement>(selector);
      if (stopped || !target) return;
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
        for (const to of ["applied", "words", "jobs"] as const) {
          await tap(`.phone-tabs [data-tab="${to}"]`);
          if (stopped) return;
          go(to);
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
    const enter = (event: PointerEvent) => {
      if (event.pointerType === "mouse") pointedAt = true;
    };
    const leave = () => {
      pointedAt = false;
      if (free()) wake?.();
    };
    phone.addEventListener("pointerdown", quit);
    phone.addEventListener("focusin", quit);
    phone.addEventListener("pointerenter", enter);
    phone.addEventListener("pointerleave", leave);
    return () => {
      stopped = true;
      timers.forEach(clearTimeout);
      seen.disconnect();
      phone.removeEventListener("pointerdown", quit);
      phone.removeEventListener("focusin", quit);
      phone.removeEventListener("pointerenter", enter);
      phone.removeEventListener("pointerleave", leave);
    };
  }, [touring]);

  const said = part ? EXPLAIN[part.key] : undefined;
  return (
    <figure ref={figure} className="phone" data-reveal aria-label="The Jobs page, on a phone"
            onPointerMove={(event) => {
              if (event.pointerType === "mouse") point(event.target);
            }}
            onPointerLeave={forget} onClick={(event) => point(event.target)}>
      <div className="phone-screen" ref={screen}>
        <div className="phone-bar" aria-hidden="true">
          <span className="brand"><Mark />Mahoraga</span>
          <span className="phone-icons">
            <i><svg viewBox="0 0 24 24"><path d="M9.5 9.2a2.6 2.6 0 115 1c-.6 1-2.5 1.5-2.5 3M12 17h.01" /></svg></i>
            <i><svg viewBox="0 0 24 24"><path d="M6 16v-5a6 6 0 1112 0v5l1.5 2h-15zM10 20.5h4" /></svg></i>
          </span>
        </div>
        <div className="phone-body" ref={body} onScroll={() => measure(pointed.current)}>
          <div key={page} className="phone-page">
            {page === "applied" ? <AppliedScreen /> : page === "words" ? <WordsScreen /> : (
              <>
                <div className="tabs" aria-hidden="true" data-explain="tabs">
                  <span className="tab on">Your jobs</span><span className="tab">Bookmarks</span>
                  <span className="tab">Add a job</span>
                </div>
                <p className="phone-title" data-explain="count">12 jobs match your words</p>
                {/* The Jobs page's search, how far back and its filters, as the app draws them on a phone. */}
                <div className="finder" aria-hidden="true" data-explain="finder">
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
        {/* The app's tab bar: the tour taps it, and so can a visitor, for the three pages the phone has. */}
        <div className="phone-tabs" aria-hidden="true" data-explain="bar">
          {PLACES.filter(([key]) => key !== "admin").map(([key, , , short]) => (
            <span key={key} data-tab={key} className={key === page ? "on" : undefined}
                  onClick={key === "jobs" || key === "applied" || key === "words" ? () => setPage(key) : undefined}>
              {short}
            </span>
          ))}
        </div>
        {touch ? <span key={touch.n} className="phone-touch" style={{ left: touch.x, top: touch.y }} /> : null}
      </div>
      {part && said ? (
        <>
          <span className="phone-focus" aria-hidden="true"
                style={{ top: part.top, left: part.left, width: part.width, height: part.height }} />
          <span className="phone-pointer" aria-hidden="true"
                style={{ "--x": `${part.left + part.width}px`, "--y": `${part.top + part.height / 2}px` } as CSSProperties} />
          <span key={part.key} className="phone-callout" role="status"
                style={{ "--y": `${part.top + part.height / 2}px` } as CSSProperties}>
            <strong>{said[0]}</strong>{said[1]}
          </span>
        </>
      ) : null}
      <figcaption className="phone-hint">
        <span className="by-pointer">Point at any part of the phone to see what it does.</span>
        <span className="by-touch">Tap any part of the phone to see what it does.</span>
      </figcaption>
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
        <ol data-explain="track" style={{ "--done": reached / (STEPS.length - 1) } as CSSProperties}>
          {STEPS.map(([key, label], index) => (
            <li key={key} className={index <= reached ? "done" : undefined}>
              <span className="dot">
                {index <= reached ? <svg viewBox="0 0 16 16"><path d="M3.5 8.4l2.9 2.9 6-6.3" /></svg> : null}
              </span>
              {label}
            </li>
          ))}
        </ol>
        <div className="progress-next" data-explain="ask">
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
        <span className="bar-action apply" data-explain="open">Open the job{PICTURES.apply}</span>
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
        <div key={key} className="phone-words" data-explain={key}>
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
