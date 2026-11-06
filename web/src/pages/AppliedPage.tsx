/** Applied: every job you said you applied to, newest first — the ones that no longer match your
 * search included — with the way back to each, and a tap on one shows all of it. Each says how far it
 * got, in your words (owner, 2026-11-03): an interview, an offer, the job, or not this time. */
import { useState } from "react";
import type { MouseEvent } from "react";

import { api, type Schema } from "../api";
import { JobView } from "../jobview";
import { useKept } from "../kept";
import { ago, useNow } from "../time";
import { Error, useAction } from "../ui";
import { latest } from "./JobsPage";

type Applied = Schema<"AppliedJob">;
type Step = Schema<"Step">;
type Stage = Step["stage"];

/** The way forward, in order; "rejected" ends it from wherever it stands. */
const STEPS: [Stage | "applied", string][] = [["applied", "Applied"], ["interview", "Interview"],
                                              ["offer", "Offer"], ["hired", "Got the job"]];
/** At each stage: the question, and the answer that moves it on. */
const NEXT: Partial<Record<Stage | "applied", [string, Stage, string]>> = {
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
      <p className="kicker">Applied</p>
      <h1>
        {jobs === null ? "Your applications…"
          : `You applied to ${jobs.length} ${jobs.length === 1 ? "job" : "jobs"}`}
      </h1>
      {jobs?.length ? (
        <p className="lede">
          {summary || "Heard back from one? Say so on it: it shows us which CVs and searches work, and we make yours better."}
        </p>
      ) : null}
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

function AppliedCard({ job, busy, onUndo, onProgress }: {
  job: Applied;
  busy: boolean;
  onUndo: () => void;
  onProgress: (progress: Step[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const now = useNow();
  const meta = (
    <p className="job-meta">
      {job.company ? <strong className="company">{job.company}</strong> : null}
      {[job.location, `Applied ${ago(job.applied_at, now)}`].filter(Boolean).join(" · ")}
    </p>
  );
  const actions = (
    <div className="job-actions">
      {(["cv", "cover"] as const).map((kind) => {
        const made = latest(job.documents)[kind];
        return made ? (
          <a key={kind} className="button small" href={made.download_url} download>
            Download your {kind === "cv" ? "CV" : "cover letter"}
          </a>
        ) : null;
      })}
      <a className="button secondary small" href={job.url} target="_blank" rel="noreferrer">Open job</a>
      <button type="button" className="secondary small" disabled={busy} onClick={onUndo}>I did not apply</button>
    </div>
  );
  // Anywhere on the card but its buttons and links opens the whole job, as on Jobs.
  const opener = (event: MouseEvent) => {
    if (!(event.target as HTMLElement).closest("a, button")) setOpen(true);
  };
  return (
    <article className="job-card" onClick={opener}>
      <h3>
        <button type="button" className="title" onClick={() => setOpen(true)}>{job.title}</button>
      </h3>
      {meta}
      <Progress job={job} onChange={onProgress} />
      {actions}
      {open ? (
        <JobView id={job.id} title={job.title} onClose={() => setOpen(false)}>
          {meta}
          {actions}
        </JobView>
      ) : null}
    </article>
  );
}

/** Where the application stands, as a row of steps, and the one question that moves it on: two
 * answers, the good one first, and Undo for a tap made by mistake. */
function Progress({ job, onChange }: { job: Applied; onChange: (progress: Step[]) => void }) {
  const stage = job.progress.at(-1)?.stage ?? "applied";
  const reached = furthest(job.progress);
  const answer = useAction(async (next: Stage) => onChange(await api.put<Step[]>(`/jobs/${job.id}/progress`, { stage: next })));
  const back = useAction(async () => onChange(await api.del<Step[]>(`/jobs/${job.id}/progress`)));
  const next = NEXT[stage];
  const busy = answer.busy || back.busy;
  return (
    <div className={stage === "rejected" ? "progress ended" : "progress"}>
      <ol aria-label="Where this application stands">
        {STEPS.map(([key, label], index) => (
          <li key={key} className={index <= reached ? "done" : undefined}
              aria-current={index === reached && stage !== "rejected" ? "step" : undefined}>
            <span className="dot" aria-hidden="true" />
            {label}
          </li>
        ))}
      </ol>
      <div className="progress-actions">
        {stage === "rejected" ? <span className="progress-note no">Not this time. On to the next one.</span>
          : stage === "hired" ? <span className="progress-note yes">You got the job. Congratulations!</span>
            : next ? (
              <>
                <span className="small muted">{next[0]}</span>
                <button type="button" className="small" disabled={busy} onClick={() => void answer.run(next[1])}>
                  {next[2]}
                </button>
                <button type="button" className="secondary small" disabled={busy}
                        onClick={() => void answer.run("rejected")}>
                  Not this time
                </button>
              </>
            ) : null}
        {job.progress.length ? (
          <button type="button" className="linklike small" disabled={busy} onClick={() => void back.run()}>Undo</button>
        ) : null}
      </div>
      <Error>{answer.error ?? back.error}</Error>
    </div>
  );
}
