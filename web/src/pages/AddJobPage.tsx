/** Add a job, the second tab of Jobs: a job found anywhere, brought here by its link. If it is here
 * already, it says so; if not, it is read from its page and kept, for you and for everyone whose words
 * it has. What the page will not say, you write in, and a job written in is yours alone. Each one you
 * add stays listed here, a tap from a CV, until you skip it or apply (owner, 2026-11-03). */
import { useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { api, type Ready, type Schema } from "../api";
import { keep, useKept } from "../kept";
import { type Mark, mark as record } from "../marks";
import { Banner, Error, Field, useAction } from "../ui";
import { JobCard, JobsTabs } from "./JobsPage";

/** The jobs they added, as `kept.ts` keeps them. */
export const ADDED = "/jobs/added";

type Job = Schema<"FeedJob">;
type Answer = Schema<"LinkAnswer">;
type Draft = Schema<"Draft">;

export function AddJobPage({ dashboard, onChanged }: {
  dashboard: Ready;
  /** "I applied" changed the count the dashboard keeps. */
  onChanged: () => void;
}) {
  const [link, setLink] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const { data: added, set: setAdded, failed } = useKept<Job[]>(ADDED);
  // Skipped here (gone), or applied (kept, with its Undo).
  const [marked, setMarked] = useState<Map<number, Mark>>(new Map());
  const show = (body: Answer) => {
    setAnswer(body);
    const job = body.job;
    if (!job) return;
    setLink("");
    setAdded((now) => [job, ...(now ?? []).filter((each) => each.id !== job.id)]);
  };
  const find = useAction(async (event: FormEvent) => {
    event.preventDefault();
    setAnswer(null);
    show(await api.post<Answer>("/jobs/link", { url: link }));
  });
  const onMark = (job: Job, kind: Mark, on: boolean) => {
    setMarked((now) => {
      const next = new Map(now);
      if (on) next.set(job.id, kind);
      else next.delete(job.id);
      return next;
    });
    // Gone from the list kept for next time at once; this one keeps its Undo while it is open.
    keep(ADDED, on ? (added ?? []).filter((each) => each.id !== job.id) : added);
    void record(job.id, kind, on).then(() => {
      if (kind === "applied") onChanged();
    });
  };

  const ai = dashboard.ai.payer !== null && dashboard.ai.profile;
  const card = (job: Job) => marked.get(job.id) === "hidden" ? null : (
    <JobCard key={job.id} job={job} ai={ai} focus={false} fresh={false} onMark={onMark}
             done={marked.get(job.id) === "applied" ? "applied" : undefined} />
  );
  const shown = answer?.job ?? null;
  const others = (added ?? []).filter((job) => job.id !== shown?.id);
  return (
    <>
      <JobsTabs on="add" />
      <p className="kicker">Add a job</p>
      <h1>Found a job somewhere else?</h1>
      <p className="lede">Paste its link here. We show it if we have it, or read it from its page and add it. Then you can make your CV for it.</p>

      <form className="toolbar" onSubmit={(event) => void find.run(event)}>
        <input type="url" required placeholder="https://…" aria-label="The job's link"
               value={link} onChange={(event) => setLink(event.target.value)} />
        <button type="submit" disabled={find.busy}>{find.busy ? "Reading the job…" : "Find this job"}</button>
      </form>
      <Error>{find.error}</Error>

      {answer ? <Said answer={answer} /> : null}
      {shown ? <div className="jobs">{card(shown)}</div> : null}
      {answer?.outcome === "draft" && answer.draft ? (
        <WriteItIn key={answer.draft.url} draft={answer.draft} onSaved={show} />
      ) : null}

      {others.length ? (
        <>
          <h2 style={{ marginTop: "2rem" }}>Jobs you added</h2>
          <div className="jobs">{others.map(card)}</div>
        </>
      ) : null}
      {failed ? <p className="error">The jobs you added did not load. Refresh the page to try again.</p> : null}
    </>
  );
}

/** What the link turned out to be, in a line. */
function Said({ answer }: { answer: Answer }) {
  const draft = answer.draft;
  const page = draft ? <a href={draft.url} target="_blank" rel="noreferrer">Open its page</a> : null;
  if (answer.outcome === "known") return <Banner level="info">We already have this job. Here it is.</Banner>;
  if (answer.outcome === "added") {
    return (
      <Banner level="info">
        {answer.job?.source === "typed" ? "Saved for you. Only you can see it, and it is ready for your CV."
          : "Added. Here it is, ready for your CV."}
      </Banner>
    );
  }
  if (answer.outcome === "closed") {
    return <Banner level="warning">This job is not taking applications any more. {page}</Banner>;
  }
  const nothing = !draft?.title && !draft?.company && !draft?.description;
  return (
    <Banner level="warning">
      {nothing ? "We could not read that page. Copy the job from it into the boxes below." :
        "We could only read part of it. Check it, fill in what is missing, and save it."} {page}
    </Banner>
  );
}

/** The job as the page gave it, for the person to finish and keep. */
function WriteItIn({ draft, onSaved }: { draft: Draft; onSaved: (answer: Answer) => void }) {
  const [job, setJob] = useState(draft);
  const save = useAction(async (event: FormEvent) => {
    event.preventDefault();
    onSaved(await api.post<Answer>("/jobs/link/save", job));
  });
  const edit = (name: keyof Draft) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setJob({ ...job, [name]: event.target.value });
  return (
    <form className="card" onSubmit={(event) => void save.run(event)}>
      <Field label="Job title">
        <input required value={job.title} onChange={edit("title")} />
      </Field>
      <Field label="Company">
        <input required value={job.company} onChange={edit("company")} />
      </Field>
      <Field label="Where" hint="A city or a country, or Remote.">
        <input value={job.location} onChange={edit("location")} />
      </Field>
      <Field label="What the job is" hint="Paste the whole description from the job's page.">
        <textarea required value={job.description} onChange={edit("description")} />
      </Field>
      <button type="submit" disabled={save.busy}>{save.busy ? "Saving…" : "Save this job"}</button>
      <Error>{save.error}</Error>
    </form>
  );
}
