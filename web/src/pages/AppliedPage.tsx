/** Applied: every job you said you applied to, newest first — the ones that no longer match your
 * search included — with the way back to each. */
import { useEffect, useState } from "react";

import { api, type Schema } from "../api";
import { ago } from "../time";
import { Error, useAction } from "../ui";

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
      <p className="lede">Every job you marked “I applied”, newest first.</p>
      {failed ? <p className="error">Your applications did not load. Refresh the page to try again.</p> : null}
      {jobs?.length === 0 ? (
        <p className="muted">
          Nothing here yet. When you apply to a job, tap “I applied” on it in <a href="#/">Jobs</a>, and
          it is kept here.
        </p>
      ) : null}
      <div className="jobs">
        {jobs?.map((job) => (
          <article key={job.id} className="job-card">
            <h3>
              <a href={job.url} target="_blank" rel="noreferrer">{job.title}</a>
            </h3>
            <p className="job-meta">
              {[job.company, job.location, `Applied ${ago(job.applied_at)}`].filter(Boolean).join(" · ")}
            </p>
            <div className="job-actions">
              <a className="button secondary small" href={job.url} target="_blank" rel="noreferrer">Open job</a>
              <button type="button" className="secondary small" disabled={undo.busy}
                      onClick={() => void undo.run(job.id)}>
                I did not apply
              </button>
            </div>
          </article>
        ))}
      </div>
      <Error>{undo.error}</Error>
    </>
  );
}
