/** The Jobs page's card, working, on a made-up job: it writes a CV or a cover letter in front of you, says
 * where you would apply, asks before a Skip, and offers an Undo after "Mark as applied". The app's own
 * classes and pictures, so it looks as the real one does. Fictional, as every example on the landing page
 * is: never real pipeline output. */
import { useEffect, useRef, useState } from "react";

import { PICTURES } from "../pages/JobsPage";
import { Prompt } from "../ui";

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

export function Demo() {
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
