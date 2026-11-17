/** Applied: every job you said you applied to, newest first — the ones that no longer match your
 * search included — with the way back to each, and a tap on one shows all of it. Each says how far it
 * got, in your words (owner, 2026-11-03): an interview, an offer, the job, or not this time. */
import { useState } from "react";
import type { CSSProperties, MouseEvent } from "react";

import { api, type Schema } from "../api";
import { JobView } from "../jobview";
import { useKept } from "../kept";
import { ago, useNow } from "../time";
import { Error, Prompt, useAction } from "../ui";
import { MORE_PICTURES, PICTURES, latest } from "./JobsPage";

type Applied = Schema<"AppliedJob">;
type Step = Schema<"Step">;
type Stage = Step["stage"];

/** The way forward, in order; "rejected" ends it from wherever it stands. The landing page's phone draws it too. */
export const STEPS: [Stage | "applied", string][] = [["applied", "Applied"], ["interview", "Interview"],
                                              ["offer", "Offer"], ["hired", "Got the job"]];
/** At each stage: the question, and the answer that moves it on. The landing page's phone asks them too. */
export const NEXT: Partial<Record<Stage | "applied", [string, Stage, string]>> = {
  applied: ["Heard back?", "interview", "I got an interview"],
  interview: ["How did it go?", "offer", "I got an offer"],
  offer: ["Did it work out?", "hired", "I got the job"],
};

/** How far an application got: the furthest step forward it reached. */
function furthest(progress: Step[]): number {
  return Math.max(0, ...progress.map((step) => STEPS.findIndex(([key]) => key === step.stage)));
}

/** "2 interviews · 1 offer": what the list adds up to, or nothing before anybody heard back. */
function tally(jobs: Applied[]): string {
  const reached = (index: number) => jobs.filter((job) => furthest(job.progress) >= index).length;
  const ended = jobs.filter((job) => job.progress.at(-1)?.stage === "rejected").length;
  const parts: [number, string, string][] = [[reached(1), "interview", "interviews"], [reached(2), "offer", "offers"],
                                             [reached(3), "job", "jobs"], [ended, "not this time", "not this time"]];
  return parts.filter(([count]) => count > 0)
    .map(([count, one, many]) => `${count} ${count === 1 ? one : many}`).join(" · ");
}

/** The applications, as `kept.ts` keeps them. */
export const APPLIED = "/applied";

export function AppliedPage() {
  const { data: jobs, set: setJobs, failed } = useKept<Applied[]>(APPLIED);
  const undo = useAction(async (id: number) => {
    await api.del(`/jobs/${id}/applied`);
    setJobs((now) => now?.filter((job) => job.id !== id) ?? null);
  });
  const progressed = (id: number, progress: Step[]) =>
    setJobs((now) => now?.map((job) => (job.id === id ? { ...job, progress } : job)) ?? null);
  const summary = jobs ? tally(jobs) : "";

  return (
    <>
      <h1>
        {jobs === null ? "Your applications…"
          : `You applied to ${jobs.length} ${jobs.length === 1 ? "job" : "jobs"}`}
      </h1>
      {summary ? <p className="lede">{summary}</p> : null}
      {failed ? <p className="error">Your applications did not load. Refresh the page to try again.</p> : null}
      {jobs?.length === 0 ? (
        <p className="muted">
          Nothing here yet. When you have applied to a job, tap “Mark as applied” on it in{" "}
          <a href="#/">Jobs</a>, and it is kept here.
        </p>
      ) : null}
      <div className="jobs">
        {jobs?.map((job) => (
          <AppliedCard key={job.id} job={job} busy={undo.busy} onUndo={() => void undo.run(job.id)}
                       onProgress={(progress) => progressed(job.id, progress)} />
        ))}
      </div>
      <Error>{undo.error}</Error>
    </>
  );
}

/** One application, as a job card is drawn on Jobs (owner, 2026-11-16: "lots of buttons shoved together"): who
 * and where, how far it got, the one question that moves it on, the documents made for it, and along its foot
 * the way back to the job's own site and, set apart, the way to take it off. */
function AppliedCard({ job, busy, onUndo, onProgress }: {
  job: Applied;
  busy: boolean;
  onUndo: () => void;
  onProgress: (progress: Step[]) => void;
}) {
  const [open, setOpen] = useState(false);
  // Taken off Applied only once they say so: what they said about it so far waits for it to come back.
  const [asking, setAsking] = useState(false);
  const now = useNow();
  const meta = (
    <p className="job-meta">
      {job.company ? <strong className="company">{job.company}</strong> : null}
      {[job.location, `Applied ${ago(job.applied_at, now)}`].filter(Boolean).join(" · ")}
    </p>
  );
  const made = latest(job.documents);
  const documents = made.cv || made.cover ? (
    <div className="job-docs">
      {(["cv", "cover"] as const).map((kind) => made[kind] ? (
        <a key={kind} className={kind === "cv" ? "button" : "button secondary"} href={made[kind].download_url} download>
          {MORE_PICTURES.download}Download {kind === "cv" ? "CV" : "cover letter"}
        </a>
      ) : null)}
    </div>
  ) : null;
  const marks = (
    <div className="job-bar">
      <a className="bar-action apply" href={job.url} target="_blank" rel="noreferrer">
        Open the job{PICTURES.apply}
      </a>
      <button type="button" className="bar-action skip" disabled={busy} onClick={() => setAsking(true)}>
        {CROSS}I did not apply
      </button>
    </div>
  );
  // Anywhere on the card but its buttons and links opens the whole job, as on Jobs; a click in a box it opened
  // is that box's own.
  const opener = (event: MouseEvent) => {
    if (!(event.target as HTMLElement).closest("a, button, dialog")) setOpen(true);
  };
  return (
    <article className="job-card" onClick={opener}>
      <h3>
        <button type="button" className="title" onClick={() => setOpen(true)}>{job.title}</button>
      </h3>
      {meta}
      <Progress job={job} onChange={onProgress} />
      {documents}
      {marks}
      {open ? (
        <JobView id={job.id} title={job.title} onClose={() => setOpen(false)}>
          {meta}
          {documents}
          {marks}
        </JobView>
      ) : null}
      {asking ? (
        <Prompt title="Take this off Applied?" onClose={() => setAsking(false)}>
          <p className="small muted">
            <strong>{job.title}</strong> leaves Applied, and can show in your jobs again.
          </p>
          <div className="row">
            <button type="button" autoFocus onClick={() => {
              setAsking(false);
              onUndo();
            }}>
              Take it off
            </button>
            <button type="button" className="secondary" onClick={() => setAsking(false)}>Keep it</button>
          </div>
        </Prompt>
      ) : null}
    </article>
  );
}

/** A cross: "I did not apply". */
const CROSS = <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>;

/** Where the application stands, as a row of steps on one track filled as far as it got, and under it the one
 * question that moves it on: two answers, the good one first, and an Undo for a tap made by mistake, a button
 * like the answers, never a link (owner, 2026-11-16). */
function Progress({ job, onChange }: { job: Applied; onChange: (progress: Step[]) => void }) {
  const stage = job.progress.at(-1)?.stage ?? "applied";
  const reached = furthest(job.progress);
  const answer = useAction(async (next: Stage) => onChange(await api.put<Step[]>(`/jobs/${job.id}/progress`, { stage: next })));
  const back = useAction(async () => onChange(await api.del<Step[]>(`/jobs/${job.id}/progress`)));
  const next = NEXT[stage];
  const busy = answer.busy || back.busy;
  return (
    <div className={stage === "rejected" ? "progress ended" : "progress"}>
      <ol aria-label="Where this application stands"
          style={{ "--done": reached / (STEPS.length - 1) } as CSSProperties}>
        {STEPS.map(([key, label], index) => (
          <li key={key} className={index <= reached ? "done" : undefined}
              aria-current={index === reached && stage !== "rejected" ? "step" : undefined}>
            <span className="dot" aria-hidden="true">
              {index <= reached ? <svg viewBox="0 0 16 16"><path d="M3.5 8.4l2.9 2.9 6-6.3" /></svg> : null}
            </span>
            {label}
          </li>
        ))}
      </ol>
      <div className="progress-next">
        {stage === "rejected" ? <p className="progress-note no">Not this time. On to the next one.</p>
          : stage === "hired" ? <p className="progress-note yes">You got the job. Congratulations!</p>
            : next ? (
              <>
                <p className="progress-ask">{next[0]}</p>
                <div className="progress-answers">
                  <button type="button" className="small" disabled={busy} onClick={() => void answer.run(next[1])}>
                    {next[2]}
                  </button>
                  <button type="button" className="secondary small" disabled={busy}
                          onClick={() => void answer.run("rejected")}>
                    Not this time
                  </button>
                </div>
              </>
            ) : null}
        {job.progress.length ? (
          <button type="button" className="secondary small undo" disabled={busy} onClick={() => void back.run()}>
            {MORE_PICTURES.undo}Undo
          </button>
        ) : null}
      </div>
      <Error>{answer.error ?? back.error}</Error>
    </div>
  );
}
