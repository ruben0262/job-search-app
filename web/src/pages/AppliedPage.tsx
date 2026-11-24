/** Applied: every job you said you applied to, newest first — the ones that no longer match your
 * search included — with the way back to each, and a tap on one shows all of it. Each says how far it
 * got, in your words (owner, 2026-11-03): an interview, an offer, the job, or not this time. Above them a
 * month's calendar of the days you applied, and below it the search and filters every list has (owner,
 * 2026-11-24: "filter all of their jobs, just like on the other pages, but to track them inside a calendar
 * visually"). */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { CSSProperties, MouseEvent } from "react";

import { api, type Schema } from "../api";
import { useBuild } from "../documents";
import { FilterSheet, Finder, FiltersOn, type Narrow, SitesSection, WhereSection, WIDE, narrowed, narrowing,
         toggled } from "../finder";
import { textsAhead } from "../jobtext";
import { JobView } from "../jobview";
import { keep, peek, useKept } from "../kept";
import { ago, useNow } from "../time";
import { Error, Prompt, useAction } from "../ui";
import { MakeDocument, MORE_PICTURES, PICTURES, latest } from "./JobsPage";

type Applications = Schema<"Applications">;
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

/** Where an application stands now, as the filter offers it: its last step, or none heard yet. */
type Standing = Stage | "applied";
const STANDINGS: [Standing, string][] = [["applied", "No answer yet"], ["interview", "Interview"], ["offer", "Offer"],
                                         ["hired", "Got the job"], ["rejected", "Not this time"]];
const standing = (progress: Step[]): Standing => progress.at(-1)?.stage ?? "applied";

/** What the page is filtered by: what every list is, the steps picked, and a day of the calendar ("2026-11-03",
 * the person's own). Kept while the site is open (`kept.ts`). */
type Picks = Narrow & { stage: Standing[]; day: string | null };
const FILTERS = `${APPLIED}/filters`;
const NONE: Picks = { q: "", ...WIDE, stage: [], day: null };

/** A day as the calendar names it, in the person's own time: "2026-11-03". */
const dayOf = (at: Date) =>
  `${at.getFullYear()}-${`${at.getMonth() + 1}`.padStart(2, "0")}-${`${at.getDate()}`.padStart(2, "0")}`;
/** The start of a day named so, in the person's own time, `ahead` days on. */
const startOf = (day: string, ahead = 0) => {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year!, month! - 1, date! + ahead);
};

/** The question for these filters: each that is on, by the name `/applied` takes it; a day as the hours it spans
 * here, which only this browser knows. */
function asked({ stage, day, ...narrow }: Picks): string {
  const params = narrowed(narrow);
  for (const each of stage) params.append("stage", each);
  if (day) {
    params.set("applied_from", startOf(day).toISOString());
    params.set("applied_to", startOf(day, 1).toISOString());
  }
  return `${params}`;
}

export function AppliedPage() {
  const [picks, setPicks] = useState<Picks>(() => peek<Picks>(FILTERS) ?? NONE);
  const set = (change: Partial<Picks>) => setPicks((now) => ({ ...now, ...change }));
  useEffect(() => keep(FILTERS, picks), [picks]);
  // Typing does not ask once a key.
  const [query, setQuery] = useState(() => asked(picks));
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(asked(picks)), picks.q ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [picks]);
  const { data, set: setData, failed } = useKept<Applications>(query ? `${APPLIED}?${query}` : APPLIED);
  // The last answer stays drawn while the next filters are asked about: the calendar does not blink away.
  const [shown, setShown] = useState<Applications | null>(data);
  useEffect(() => {
    if (data) setShown(data);
  }, [data]);
  const jobs = shown?.jobs ?? null;
  const [choosing, setChoosing] = useState(false);

  // Taken off, it leaves the list, its day on the calendar and its count among the steps.
  const undo = useAction(async (id: number) => {
    await api.del(`/jobs/${id}/applied`);
    setData((now) => {
      const gone = now?.jobs.find((job) => job.id === id);
      if (!now || !gone) return now;
      const at = now.dates.indexOf(gone.applied_at);
      const was = standing(gone.progress);
      return { ...now, total: now.total - 1, jobs: now.jobs.filter((job) => job.id !== id),
               dates: now.dates.filter((_, index) => index !== at),
               stages: { ...now.stages, [was]: (now.stages[was] ?? 1) - 1 } };
    });
  });
  // A step said here moves the job and the counts the filter offers; the job stays in the list until it is
  // asked for again, so an answer does not take the card from under the tap.
  const progressed = (id: number, progress: Step[]) => setData((now) => {
    const was = now?.jobs.find((job) => job.id === id);
    if (!now || !was) return now;
    const stages = { ...now.stages, [standing(was.progress)]: (now.stages[standing(was.progress)] ?? 1) - 1 };
    stages[standing(progress)] = (stages[standing(progress)] ?? 0) + 1;
    return { ...now, stages, jobs: now.jobs.map((job) => (job.id === id ? { ...job, progress } : job)) };
  });
  // Their texts, so each opens at once (`jobtext.ts`).
  useEffect(() => textsAhead((jobs ?? []).map((job) => job.id)), [jobs]);

  const offers = shown?.offers ?? null;
  const on: [string, Partial<Picks>][] = [
    ...picks.stage.map((each): [string, Partial<Picks>] => [STANDINGS.find(([key]) => key === each)?.[1] ?? each,
      { stage: picks.stage.filter((other) => other !== each) }]),
    ...(picks.day ? [[`Applied on ${startOf(picks.day).toLocaleDateString(undefined, { day: "numeric", month: "long" })}`,
      { day: null }] as [string, Partial<Picks>]] : []),
    ...(offers ? narrowing(picks, offers) : []),
  ];
  const filtered = picks.q.trim() !== "" || on.length > 0;
  const clear = () => setPicks(NONE);
  const total = shown?.total ?? 0;
  const summary = jobs ? tally(jobs) : "";
  const applications = (n: number) => `${n} ${n === 1 ? "application" : "applications"}`;

  return (
    <>
      <h1>
        {jobs === null ? "Your applications…"
          : filtered ? `${jobs.length} of your ${applications(total)}`
            : `You applied to ${total} ${total === 1 ? "job" : "jobs"}`}
      </h1>
      {summary ? <p className="lede">{summary}</p> : null}
      {failed ? <p className="error">Your applications did not load. Refresh the page to try again.</p> : null}
      {shown?.total === 0 ? (
        <p className="muted">
          Nothing here yet. When you have applied to a job, tap “Mark as applied” on it in{" "}
          <a href="/jobs">Jobs</a>, and it is kept here.
        </p>
      ) : null}
      {shown && shown.total > 0 ? (
        <>
          <Finder query={picks.q} onQuery={(q) => set({ q })} label="Search applications" on={on.length}
                  onOpen={() => setChoosing(true)}>
            <CalendarButton dates={shown.dates} day={picks.day} onDay={(day) => set({ day })} />
          </Finder>
          <FiltersOn on={on} set={set} clear={clear} />
        </>
      ) : null}
      {choosing && shown && offers ? (
        <FilterSheet on={on.length > 0} clear={clear} onClose={() => setChoosing(false)}
                     showing={`Show ${applications(jobs?.length ?? 0)}`}>
          <section className="filter-group">
            <h3>Where it stands</h3>
            <p className="filter-hint">Show only the applications at the steps you tap.</p>
            <div className="chips" role="group" aria-label="Where it stands">
              {STANDINGS.filter(([key]) => shown.stages[key] || picks.stage.includes(key)).map(([key, label]) => {
                const pressed = picks.stage.includes(key);
                return (
                  <button key={key} type="button" className={pressed ? "chip on" : "chip"} aria-pressed={pressed}
                          onClick={() => set({ stage: toggled(picks.stage, key) as Standing[] })}>
                    {label} <span className="count">{shown.stages[key] ?? 0}</span>
                  </button>
                );
              })}
            </div>
          </section>
          <WhereSection narrow={picks} set={set} offers={offers} />
          <SitesSection narrow={picks} set={set} offers={offers} />
        </FilterSheet>
      ) : null}
      {jobs?.length === 0 && filtered ? (
        <p className="muted">
          None of your applications match.{" "}
          <button type="button" className="secondary small" onClick={clear}>Clear filters</button>
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

/** The days of a week, Monday first, as a European calendar has them. */
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** A page of a calendar, with the two rings for its binding. */
const CALENDAR = <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.5h14a1 1 0 011 1V19a1 1 0 01-1 1H5a1 1 0 01-1-1V6.5a1 1 0 011-1zM4 10h16M8.5 3.5v4M15.5 3.5v4" /></svg>;

/** "Calendar", before the filters (owner, 2026-11-24: "a button called calendar that does a popup below the button of
 * the calendar and users can click dates to check out what they applied for specifically"): the month opens under
 * it, over the list, and closes when a day is picked, on a tap outside it or on Escape, giving the button its focus
 * back. */
function CalendarButton({ dates, day, onDay }: {
  dates: string[];
  day: string | null;
  onDay: (day: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const holder = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!holder.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <div className="calendar-holder" ref={holder}>
      <button ref={button} type="button" className="secondary finder-open" aria-haspopup="dialog" aria-expanded={open}
              aria-controls={id} onClick={() => setOpen(!open)}>
        {CALENDAR}Calendar
      </button>
      {open ? (
        <Calendar id={id} dates={dates} day={day} onDay={(picked) => {
          onDay(picked);
          setOpen(false);
          button.current?.focus();
        }} />
      ) : null}
    </div>
  );
}

/** A month of the days they applied (owner, 2026-11-24: "a calendar, where people can see their consistent efforts
 * throughout a month via days"): each day filled as deep as it was busy, with how many they sent; a tap on one lists
 * that day's. Under it what the month adds up to: applications, days applied, and the longest run of days in a
 * row. Each counted in their own time, from when each application was marked. */
function Calendar({ id, dates, day, onDay }: {
  id: string;
  dates: string[];
  day: string | null;
  onDay: (day: string | null) => void;
}) {
  const now = new Date(useNow());
  const counts = useMemo(() => {
    const found = new Map<string, number>();
    for (const at of dates) {
      const key = dayOf(new Date(at));
      found.set(key, (found.get(key) ?? 0) + 1);
    }
    return found;
  }, [dates]);
  // The month shown: this one, or the one a day picked is in, back as far as the first application.
  const [month, setMonth] = useState(() => (day ? startOf(day) : now));
  const year = month.getFullYear();
  const index = month.getMonth();
  const first = dates.length ? new Date(dates[dates.length - 1]!) : now;
  const earliest = first.getFullYear() * 12 + first.getMonth();
  const at = year * 12 + index;
  const latest = now.getFullYear() * 12 + now.getMonth();
  const length = new Date(year, index + 1, 0).getDate();
  const days = Array.from({ length }, (_, i) => dayOf(new Date(year, index, i + 1)));
  const sent = days.map((each) => counts.get(each) ?? 0);
  let run = 0;
  let longest = 0;
  for (const count of sent) {
    run = count ? run + 1 : 0;
    longest = Math.max(longest, run);
  }
  const today = dayOf(now);
  const name = month.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const step = (by: number) => setMonth(new Date(year, index + by, 1));
  return (
    <section id={id} className="calendar" role="dialog" aria-label={`Your applications in ${name}`}>
      <div className="calendar-head">
        <button type="button" className="calendar-step" aria-label="The month before" disabled={at <= earliest}
                onClick={() => step(-1)}>‹</button>
        <h2>{name}</h2>
        <button type="button" className="calendar-step" aria-label="The month after" disabled={at >= latest}
                onClick={() => step(1)}>›</button>
      </div>
      <ol className="calendar-days">
        {WEEKDAYS.map((weekday) => <li key={weekday} className="calendar-weekday" aria-hidden="true">{weekday}</li>)}
        {/* The first day under its weekday, Monday first. */}
        {Array.from({ length: (new Date(year, index, 1).getDay() + 6) % 7 }, (_, i) => (
          <li key={`before-${i}`} aria-hidden="true" />
        ))}
        {days.map((each, i) => {
          const count = sent[i]!;
          const said = `${startOf(each).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}: ${
            count ? `${count} ${count === 1 ? "application" : "applications"}` : "none"}`;
          const level = count >= 4 ? 3 : count >= 2 ? 2 : count;
          const kind = ["calendar-day", each === today && "today", each > today && "ahead"].filter(Boolean).join(" ");
          return (
            <li key={each}>
              {count ? (
                <button type="button" className={kind} data-level={level} aria-label={said} aria-pressed={day === each}
                        onClick={() => onDay(day === each ? null : each)}>
                  <span className="calendar-date">{i + 1}</span>
                  <span className="calendar-count" aria-hidden="true">{count}</span>
                </button>
              ) : (
                <span className={kind} aria-label={said} role="img">
                  <span className="calendar-date">{i + 1}</span>
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <dl className="calendar-sums">
        <div><dt>Applications</dt><dd>{sent.reduce((sum, count) => sum + count, 0)}</dd></div>
        <div><dt>Days applied</dt><dd>{sent.filter(Boolean).length}</dd></div>
        <div><dt>Days in a row</dt><dd>{longest}</dd></div>
      </dl>
    </section>
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
  // Made, being made, or to make: the same buttons as on Jobs, as a job marked applied may still want its
  // documents (owner, 2026-11-19).
  const made = latest(job.documents);
  const cv = useBuild(job.id, "cv");
  const cover = useBuild(job.id, "cover");
  const documents = (
    <div className="job-docs">
      <MakeDocument doc={cv} kind="cv" existing={made.cv} />
      <MakeDocument doc={cover} kind="cover" existing={made.cover} />
    </div>
  );
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
    <article className="job-card" onClick={opener} onPointerEnter={() => textsAhead([job.id])}>
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
