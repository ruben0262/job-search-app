/** Applied: every job you said you applied to, newest first — the ones that no longer match your
 * search included — with the way back to each, and a tap on one shows all of it. */
import { useEffect, useState } from "react";
import type { MouseEvent } from "react";

import { api, type Schema } from "../api";
import { JobView } from "../jobview";
import { ago, useNow } from "../time";
import { Error, useAction } from "../ui";
import { latest } from "./JobsPage";

type Applied = Schema<"AppliedJob">;

export function AppliedPage() {
  const [jobs, setJobs] = useState<Applied[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api.get<Applied[]>("/applied").then(setJobs).catch(() => setFailed(true));
  }, []);
  const undo = useAction(async (id: number) => {
    await api.del(`/jobs/${id}/applied`);
    setJobs((now) => now?.filter((job) => job.id !== id) ?? null);
  });

  return (
    <>
      <p className="kicker">Applied</p>
      <h1>
        {jobs === null ? "Your applications…"
          : `You applied to ${jobs.length} ${jobs.length === 1 ? "job" : "jobs"}`}
      </h1>
      {failed ? <p className="error">Your applications did not load. Refresh the page to try again.</p> : null}
      {jobs?.length === 0 ? (
        <p className="muted">
          Nothing here yet. When you apply to a job, tap “I applied” on it in <a href="#/">Jobs</a>, and
          it is kept here.
        </p>
      ) : null}
      <div className="jobs">
        {jobs?.map((job) => (
          <AppliedCard key={job.id} job={job} busy={undo.busy} onUndo={() => void undo.run(job.id)} />
        ))}
      </div>
      <Error>{undo.error}</Error>
    </>
  );
}

function AppliedCard({ job, busy, onUndo }: { job: Applied; busy: boolean; onUndo: () => void }) {
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
