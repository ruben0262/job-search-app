/** Jobs, the home page: the jobs that match your words, each a tap from a CV and a cover letter written
 * for it with their own AI account, and a tap on one shows all of it. New ones are announced, never
 * pushed in under the reader. Every alert lands here, on `#/feed?job=<id>`. */
import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";

import { api, request, type Ready, type Schema } from "../api";
import { useBuild } from "../documents";
import { JobView } from "../jobview";
import { keep, peek } from "../kept";
import { type Mark, mark as record, send, unsent } from "../marks";
import { POLL_MS } from "../notifications";
import { PLACES } from "../places";
import { ago, useNow } from "../time";
import { Banner, Error, Prompt, useAction } from "../ui";

type Feed = Schema<"Feed">;
type Filters = Schema<"JobFilters">;
type Job = Schema<"FeedJob">;
type Made = Schema<"JobDocument">;
type Build = ReturnType<typeof useBuild>;

/** The newest of each kind, which is the one a card offers. */
export function latest(documents: Made[]): Partial<Record<Made["kind"], Made>> {
  const found: Partial<Record<Made["kind"], Made>> = {};
  for (const each of documents) found[each.kind] ??= each;
  return found;
}

/** How long a job marked applied keeps its Undo before it leaves the page. */
const UNDO_MS = 6000;

/** "Posted": hours back, as the server's `since_hours` takes them. */
const POSTED: [string, number | null][] = [["Any time", null], ["Last 24 hours", 24], ["Last 3 days", 72],
                                           ["Last week", 168]];

/** The pictures on a job's own buttons, in each button's colour: out to the site it was posted on, the tick
 * that is Applied's picture, and an eye shut on a job not shown again. The landing page's card has them too. */
export const PICTURES = {
  apply: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13.5 4.5h6v6M19.5 4.5l-8 8M18 13.5v5a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 014 18.5v-11A1.5 1.5 0 015.5 6h5" /></svg>,
  applied: <svg viewBox="0 0 24 24" aria-hidden="true">{PLACES.find(([key]) => key === "applied")?.[2]}</svg>,
  skip: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM14.5 12a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0zM4 4l16 16" /></svg>,
};

/** A time a job stays listed, as people say it: "24 hours", "3 days". */
const span = (hours: number) => (hours < 48 || hours % 24 ? `${hours} hours` : `${hours / 24} days`);

/** The orders the list comes in: its words, when the board says it was posted, when it arrived here. */
const ORDERS = [["best", "Most of your words first"], ["newest", "Newest posted first"],
                ["arrived", "Just arrived first"]] as const;
type Order = (typeof ORDERS)[number][0];

/** A country's name from its code, as the browser knows it in English. */
const REGIONS = new Intl.DisplayNames(["en"], { type: "region" });

/** The list as it first opens, kept (`kept.ts`): coming back to Jobs shows it at once. */
/** The filters as this tab last had them (`kept.ts`), over the saved ones the dashboard brought. */
const FILTERS = "/feed/filters";

/** The feed's question for these filters: each one that is on, by the name `/feed` takes it. Its answer is
 * kept under it, so the page comes back drawn at once, whatever they had chosen. */
function asked(filters: Filters, offset = 0): URLSearchParams {
  const { q, remote, since_hours, source, country, city, sort, made, word = [], min_words } = filters;
  const params = new URLSearchParams({ offset: `${offset}` });
  if (q.trim()) params.set("q", q.trim());
  if (remote) params.set("remote", "true");
  if (since_hours) params.set("since_hours", `${since_hours}`);
  if (source) params.set("source", source);
  if (country) params.set("country", country);
  if (city) params.set("city", city);
  if (sort !== "best") params.set("sort", sort);
  if (made === false) params.set("made", "false");
  for (const each of word) params.append("word", each);
  if (min_words > 1) params.set("min_words", `${min_words}`);
  return params;
}

/** Jobs has two tabs: the jobs that match your words, and one brought in by its link. */
export function JobsTabs({ on }: { on: "list" | "add" }) {
  return (
    <nav className="tabs split" aria-label="Jobs">
      <a className={on === "list" ? "tab on" : "tab"} href="#/" aria-current={on === "list" ? "page" : undefined}>
        Your jobs
      </a>
      <a className={on === "add" ? "tab on" : "tab"} href="#/add" aria-current={on === "add" ? "page" : undefined}>
        Add a job
      </a>
    </nav>
  );
}

export function JobsPage({ dashboard, focus, onChanged }: {
  dashboard: Ready;
  /** The job an alert's link names, if they arrived from one. */
  focus: number | null;
  /** "Mark as applied" changed the count the dashboard keeps. */
  onChanged: () => void;
}) {
  // As they left them, here or on another device (owner, 2026-11-07).
  const [filters, setFilters] = useState<Filters>(() => peek<Filters>(FILTERS) ?? dashboard.job_filters);
  const set = (change: Partial<Filters>) => setFilters((now) => ({ ...now, ...change }));
  const { q: query, remote, since_hours: since, source, country, city, sort: order, min_words: atLeast } = filters;
  const picked = filters.word ?? [];
  const noCv = filters.made === false;
  const cached = peek<Feed>(`/feed?${asked(filters)}`);
  // The boards and words their jobs have, kept from the first answer: a filtered one still offers them all.
  const [boards, setBoards] = useState<Feed["sources"]>(cached?.sources ?? []);
  const [words, setWords] = useState<Feed["words"]>(cached?.words ?? []);
  const [countries, setCountries] = useState<Feed["countries"]>(cached?.countries ?? []);
  const [cities, setCities] = useState<Feed["cities"]>(cached?.cities ?? []);
  const [feed, setFeed] = useState<Feed | null>(cached);
  const [failed, setFailed] = useState(false);
  // A read under way: a week of jobs takes longer than a day's, so the list says it is coming.
  const [busy, setBusy] = useState(false);
  // Skipped or applied here, by what: still counted in `feed.total`, so the heading subtracts them.
  // A skip, asked about first, is gone at once; "Mark as applied" keeps an Undo for a moment.
  const [marked, setMarked] = useState<Map<number, Mark>>(new Map());
  const [gone, setGone] = useState<Set<number>>(new Set());
  // How many jobs reached the list since it was read and how long the list is now, and when the one
  // before it was read: what the "new jobs" button says, and which cards it marks new.
  const [news, setNews] = useState({ arrived: 0, total: 0 });
  const [seenUntil, setSeenUntil] = useState<string | null>(null);
  const shown = (id: number) => !gone.has(id);
  // Narrowed past their words. The time chosen under "Posted" and the order are how the list is shown, not
  // what is in it, so they stay when the rest is cleared.
  const filtered = query.trim() !== "" || remote || source !== "" || country !== "" || city !== "" || noCv
    || picked.length > 0 || atLeast > 1;
  const clear = () => set({ q: "", remote: false, source: "", country: "", city: "", made: null, word: [],
                            min_words: 1 });

  // Saved as they change, for the next time Jobs opens: a moment after the last change, and at once on
  // leaving, for another page or by closing the tab (`keepalive`). One that fails is sent again with the
  // next change; this tab keeps them meanwhile.
  const opened = useRef(filters);
  const unsaved = useRef<Filters | null>(null);
  const save = useCallback(() => {
    if (unsaved.current) {
      void request(FILTERS, { method: "PUT", body: unsaved.current, keepalive: true }).catch(() => undefined);
    }
    unsaved.current = null;
  }, []);
  useEffect(() => {
    keep(FILTERS, filters);
    if (filters === opened.current) return;
    unsaved.current = filters;
    const timer = window.setTimeout(save, 400);
    return () => window.clearTimeout(timer);
  }, [filters, save]);
  useEffect(() => {
    addEventListener("pagehide", save);
    return () => {
      removeEventListener("pagehide", save);
      save();
    };
  }, [save]);

  // A page is as long as the server's own page (`FEED_PAGE`): the next one starts where this one ends.
  const read = useCallback(async (offset: number, foundAfter?: string): Promise<Feed> => {
    const params = asked(filters, offset);
    if (foundAfter) {
      params.set("found_after", foundAfter);
      params.set("limit", "1");
    }
    return api.get<Feed>(`/feed?${params}`);
  }, [filters]);

  const reload = useCallback(() => {
    setBusy(true);
    // Marks this browser still holds go first, so the answer already leaves them out.
    void send().finally(() => read(0).then((body) => {
      keep(`/feed?${asked(filters)}`, body);
      setBoards(body.sources);
      setWords(body.words);
      setCountries(body.countries);
      setCities(body.cities);
      setFeed(body);
      setMarked(new Map());
      setGone(unsent());
      setNews({ arrived: 0, total: 0 });
      setFailed(false);
    }).catch(() => setFailed(true)).finally(() => setBusy(false)));
  }, [read, filters]);

  // Typing does not fire a request per key.
  useEffect(() => {
    const timer = window.setTimeout(reload, query ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [reload, query]);

  // New jobs are counted, not added: the list never moves under somebody reading it.
  const asOf = feed?.as_of;
  useEffect(() => {
    if (!asOf) return;
    const look = () => {
      if (document.visibilityState === "visible") {
        read(0, asOf).then((body) => setNews({ arrived: body.arrived ?? 0, total: body.total })).catch(() => undefined);
      }
    };
    const timer = window.setInterval(look, POLL_MS);
    document.addEventListener("visibilitychange", look);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", look);
    };
  }, [asOf, read]);

  const showNew = () => {
    setSeenUntil(asOf ?? null);
    window.scrollTo({ top: 0, behavior: "smooth" });
    reload();
  };

  // Marked jobs have left the server's list, so the next page starts that many earlier, and its
  // total no longer counts them while this one does.
  const more = useAction(async () => {
    if (!feed) return;
    const next = await read(feed.jobs.filter((job) => !marked.has(job.id)).length);
    const have = new Set(feed.jobs.map((job) => job.id));
    setFeed({ ...next, total: next.total + marked.size, as_of: feed.as_of,
              jobs: [...feed.jobs, ...next.jobs.filter((job) => !have.has(job.id))] });
  });

  // Off the page, sent when it can be, and kept until it is: see `marks.ts`. An Undo stops the card
  // leaving.
  const leaving = useRef(new Map<number, number>());
  const onMark = (job: Job, kind: Mark, on: boolean) => {
    setMarked((now) => {
      const next = new Map(now);
      if (on) next.set(job.id, kind);
      else next.delete(job.id);
      return next;
    });
    window.clearTimeout(leaving.current.get(job.id));
    const leave = () => setGone((now) => new Set([...now, job.id]));
    if (on && kind === "hidden") leave();
    else if (on) leaving.current.set(job.id, window.setTimeout(leave, UNDO_MS));
    void record(job.id, kind, on).then(() => {
      if (kind === "applied") onChanged();
    });
  };

  const total = feed ? feed.total - marked.size : 0;
  // How long the longest-kept jobs stay, once an answer says: "Any time" under "Posted" is all of it.
  const longest = feed?.shelf_longest_hours ?? Infinity;
  // What "Posted" is set to, in a sentence: "the last 24 hours".
  const posted = since && since < longest
    ? (POSTED.find(([, hours]) => hours === since)?.[0] ?? `Last ${span(since)}`).toLowerCase() : null;
  // The new ones, and the older ones that left meanwhile, past their shelf life: the list a refresh brings
  // is this one, plus the first, less the second.
  const fresh = news.arrived;
  const left = Math.max(0, total + fresh - news.total);
  const card = (job: Job, isFocus = false) => (
    <JobCard key={job.id} job={job} focus={isFocus} done={marked.get(job.id)} onMark={onMark}
             fresh={seenUntil !== null && job.described_at !== null && job.described_at > seenUntil} />
  );
  return (
    <>
      <JobsTabs on="list" />
      {dashboard.banners.map((banner) => (
        <Banner key={banner.key} level={banner.level}>
          <span>{banner.text}</span>{" "}
          <a href="#/settings">
            {banner.key === "telegram_broken" ? "Fix it in Settings"
              : banner.key === "paused" ? "Turn them back on" : "Turn on alerts"}
          </a>
        </Banner>
      ))}

      {fresh > 0 ? (
        <button type="button" className="fresh-jobs" onClick={showNew}>
          {fresh} new {fresh === 1 ? "job" : "jobs"}
          {left ? ` · ${left} older ${left === 1 ? "one" : "ones"} gone` : ""} · Show {fresh === 1 ? "it" : "them"}
        </button>
      ) : null}

      {/* "+" when more matched than one read takes in: these are the newest of them. */}
      <h1>
        {feed === null
          ? "Finding your jobs…"
          : `${total}${feed.cut ? "+" : ""} ${total === 1 && !feed.cut ? "job matches" : "jobs match"} your ${
            filtered ? "search" : "words"}`}
      </h1>
      {/* How long a job stays here, as Admin sets it: a job leaving is then no surprise (owner, 2026-11-06). */}
      {feed ? (
        <p className="small muted">
          Jobs stay here for {span(feed.shelf_hours)}
          {feed.shelf_longest_hours > feed.shelf_hours
            ? `, or ${span(feed.shelf_longest_hours)} from slower boards` : ""}.
          {feed.cut ? " More match your words than the page reads at once, so these are the newest." : ""}
        </p>
      ) : null}
      {dashboard.applied ? (
        <p className="small muted">
          You have applied to {dashboard.applied} {dashboard.applied === 1 ? "job" : "jobs"}.{" "}
          <a href="#/applied">See them</a>
        </p>
      ) : null}

      <div className="toolbar">
        <input type="search" placeholder="Search your jobs" aria-label="Search your jobs" value={query}
               onChange={(event) => set({ q: event.target.value })} />
      </div>
      <div className="filters">
        {/* Only the times shorter than jobs stay: "Any time" is all of them. */}
        <select aria-label="Posted" value={since && since < longest ? since : ""}
                onChange={(event) => set({ since_hours: event.target.value ? Number(event.target.value) : null })}>
          {POSTED.filter(([, hours]) => hours === null || hours < longest).map(([label, hours]) => (
            <option key={label} value={hours ?? ""}>{label}</option>
          ))}
        </select>
        <select aria-label="Job board" value={source} onChange={(event) => set({ source: event.target.value })}>
          <option value="">All boards</option>
          {boards.map((each) => (
            <option key={each.name} value={each.name}>{each.label} ({each.jobs})</option>
          ))}
        </select>
        {countries.length > 1 || country ? (
          <select aria-label="Country" value={country} onChange={(event) => set({ country: event.target.value })}>
            <option value="">All countries</option>
            {countries.map((each) => (
              <option key={each.value} value={each.value}>{each.label} ({each.jobs})</option>
            ))}
          </select>
        ) : null}
        {cities.length > 1 || city ? (
          <select aria-label="City" value={city} onChange={(event) => set({ city: event.target.value })}>
            <option value="">All cities</option>
            {cities.map((each) => <option key={each.value} value={each.value}>{each.label} ({each.jobs})</option>)}
          </select>
        ) : null}
        <select aria-label="Order" value={order} onChange={(event) => set({ sort: event.target.value as Order })}>
          {ORDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        {words.length > 1 ? (
          <select aria-label="How many of your words" value={atLeast}
                  onChange={(event) => set({ min_words: Number(event.target.value) })}>
            <option value={1}>Any word</option>
            {[2, 3, 4].filter((count) => count <= words.length).map((count) => (
              <option key={count} value={count}>{count}+ words</option>
            ))}
          </select>
        ) : null}
        {([["Remote only", remote, { remote: !remote }], ["No CV yet", noCv, { made: noCv ? null : false }]] as const)
          .map(([label, on, change]) => (
          <button key={label} type="button" className={on ? "chip on" : "chip"} aria-pressed={on}
                  onClick={() => set(change)}>
            {label}
          </button>
        ))}
        {filtered ? <button type="button" className="linklike small" onClick={clear}>Clear filters</button> : null}
      </div>
      {words.length > 1 ? (
        <div className="filters words-filter" role="group" aria-label="Only jobs with">
          <span className="small muted">Only jobs with:</span>
          {words.map((word) => {
            const on = picked.includes(word.name);
            return (
              <button key={word.name} type="button" className={on ? "chip on" : "chip"} aria-pressed={on}
                      onClick={() => set({ word: on ? picked.filter((each) => each !== word.name) : [...picked, word.name] })}>
                {word.name} <span className="count">{word.jobs}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      {focus !== null && shown(focus) ? (
        <Focused id={focus} card={(job) => card(job, true)} />
      ) : null}

      {failed ? <p className="error">Your jobs did not load. Refresh the page to try again.</p> : null}
      {feed && feed.jobs.length === 0 ? (
        <p className="muted">
          {filtered ? "Nothing matches these filters." : posted ? (
            <>
              Nothing from the {posted} matches your words yet. Choose a longer time under “Posted”, or{" "}
              <a href="#/words">add more words</a>.
            </>
          ) : (
            <>
              No jobs match your words yet. New jobs come in all day, or{" "}
              <a href="#/words">add more words</a>.
            </>
          )}
        </p>
      ) : null}
      <div className="jobs" aria-busy={busy || undefined}>
        {feed?.jobs.filter((job) => job.id !== focus && shown(job.id)).map((job) => card(job))}
      </div>
      {feed && feed.jobs.length < feed.total ? (
        <button type="button" className="secondary more" disabled={more.busy} onClick={() => void more.run()}>
          {more.busy ? "Loading more…" : "Show more jobs"}
        </button>
      ) : null}
      <Error>{more.error}</Error>
    </>
  );
}

/** The job an alert pointed at, first and marked. Gone from their list, it says so and still offers
 * the documents: the alert was for a real job, and it may be the one they want to apply to. */
function Focused({ id, card }: { id: number; card: (job: Job) => ReactNode }) {
  const [job, setJob] = useState<Job | null | undefined>(undefined);
  const cv = useBuild(id, "cv");
  const cover = useBuild(id, "cover");
  useEffect(() => {
    setJob(undefined);
    api.get<Feed>(`/feed?job=${id}`).then((body) => setJob(body.jobs[0] ?? null)).catch(() => setJob(null));
  }, [id]);

  if (job === undefined) return null;
  if (job) return card(job);
  return (
    <article className="job-card focus">
      <p>This job is not in your list any more. You may have skipped it or applied, or it is too old.</p>
      <div className="job-actions">
        <MakeDocument doc={cv} kind="cv" />
        <MakeDocument doc={cover} kind="cover" />
      </div>
    </article>
  );
}

/** Where a job is: its company, its country named plainly, and the rest of the place. */
function Where({ job }: { job: Job }) {
  const now = useNow();
  let country: string | null = null;
  try {
    country = job.country ? REGIONS.of(job.country) ?? null : null;
  } catch {
    // A code the browser does not know: the place still says it.
  }
  // "Berlin, Germany" beside "Germany" says it twice.
  const parts = (job.location ?? "").split(",").map((part) => part.trim()).filter(Boolean);
  if (country && parts[parts.length - 1]?.toLowerCase() === country.toLowerCase()) parts.pop();
  const rest = parts.join(", ");
  const remote = job.is_remote && !/remote/i.test(rest) ? "Remote" : null;
  const age = ago(job.posted_at ?? job.first_seen_at, now);
  const when = age ? `${job.posted_at ? "Posted" : "Found"} ${age}` : null;
  return (
    <p className="job-meta">
      {job.company ? <strong className="company">{job.company}</strong> : null}
      {country ? <span className="country">{country}</span> : null}
      {[rest, remote, when].filter(Boolean).join(" · ")}
    </p>
  );
}

export function JobCard({ job, focus, done, fresh, onMark }: {
  job: Job;
  focus: boolean;
  /** Marked applied here, and still showing its Undo. */
  done?: Mark;
  /** Found since the list before this one was read. */
  fresh: boolean;
  onMark: (job: Job, kind: Mark, on: boolean) => void;
}) {
  // Held here, so the card and its open view are one build each: made in one, ready in both.
  const cv = useBuild(job.id, "cv");
  const cover = useBuild(job.id, "cover");
  const [open, setOpen] = useState(false);
  // A Skip asks first: a job skipped is never shown again (owner, 2026-11-02).
  const [skipping, setSkipping] = useState(false);
  // Made before (kept, so a refresh still shows it) or made now: what colours the card.
  const made = new Set(job.documents.map((each) => each.kind));
  if (cv.build?.state === "ready") made.add("cv");
  if (cover.build?.state === "ready") made.add("cover");

  if (done) {
    return (
      <article className="job-card gone">
        <span className="muted small">Moved to Applied: {job.title}</span>
        <button type="button" className="linklike" onClick={() => onMark(job, done, false)}>Undo</button>
      </article>
    );
  }

  // For everyone (owner, 2026-11-10): pressed without what a document needs, a toast says what.
  const existing = latest(job.documents);
  const documents = (
    <div className="job-actions">
      <MakeDocument doc={cv} kind="cv" existing={existing.cv} />
      <MakeDocument doc={cover} kind="cover" existing={existing.cover} />
    </div>
  );
  // Nobody applies here: the job opens where it was posted, and they apply there, then say so here
  // (owner, 2026-11-06). In that order, and Skip, the way out, apart from both.
  const marks = (
    <div className="job-actions marks">
      <a className="button secondary small" href={job.url} target="_blank" rel="noreferrer">
        Apply on {job.apply_on}
        {PICTURES.apply}
      </a>
      <button type="button" className="secondary small" onClick={() => onMark(job, "applied", true)}>
        {PICTURES.applied}Mark as applied
      </button>
      <button type="button" className="secondary small skip" onClick={() => setSkipping(true)}>
        {PICTURES.skip}Skip
      </button>
    </div>
  );
  const head = (
    <>
      {made.size || fresh ? (
        <p className="made-badges">
          {fresh ? <span className="made-badge new">New</span> : null}
          {made.has("cv") ? <span className="made-badge cv">CV made</span> : null}
          {made.has("cover") ? <span className="made-badge cover">Cover letter made</span> : null}
        </p>
      ) : null}
      <Where job={job} />
      {job.salary ? <p className="job-meta">{money(job.salary)}</p> : null}
    </>
  );
  // A job somebody added by its link is on the page for that, not for their words.
  const why = job.words.length > 0 && (
    <p className="why">
      Why:{" "}
      {job.words.map((word) => <span key={word} className="word green">{word}</span>)}
    </p>
  );
  // Anywhere on the card but its buttons and links opens the whole job; a click in a box it opened
  // is that box's own.
  const opener = (event: MouseEvent) => {
    if (!(event.target as HTMLElement).closest("a, button, input, select, label, dialog")) setOpen(true);
  };
  return (
    <article onClick={opener}
             className={["job-card", focus && "focus", made.has("cv") && "made-cv", made.has("cover") && "made-cover"]
               .filter(Boolean).join(" ")}>
      <h3>
        <button type="button" className="title" onClick={() => setOpen(true)}>{job.title}</button>
      </h3>
      {head}
      {job.summary ? (
        <p className="summary">
          {job.summary}{" "}
          {job.more ? (
            <button type="button" className="linklike small" onClick={() => setOpen(true)}>Read it all</button>
          ) : null}
        </p>
      ) : null}
      {why}
      {documents}
      {marks}
      {open ? (
        <JobView id={job.id} title={job.title} text={job.more ? undefined : job.summary}
                 onClose={() => setOpen(false)}>
          {head}
          {why}
          {documents}
          {marks}
        </JobView>
      ) : null}
      {skipping ? (
        <Prompt title="Skip this job?" onClose={() => setSkipping(false)}>
          <p className="small muted"><strong>{job.title}</strong> leaves your list and is not shown to you again.</p>
          <div className="row">
            <button type="button" autoFocus onClick={() => onMark(job, "hidden", true)}>Skip it</button>
            <button type="button" className="secondary" onClick={() => setSkipping(false)}>Keep it</button>
          </div>
        </Prompt>
      ) : null}
    </article>
  );
}

/** One document for one job: a button, then the words while it is made, then the download — and
 * the download straight away for one made before and kept. */
function MakeDocument({ doc, kind, existing }: { doc: Build; kind: "cv" | "cover"; existing?: Made }) {
  const { build, making, start, error, needsConsent } = doc;
  const name = kind === "cv" ? "CV" : "cover letter";
  const ready = build?.state === "ready" && build.download_url ? build.download_url : null;
  if (ready || (!build && existing)) {
    return (
      <span className="make">
        <a className="button" href={ready ?? existing!.download_url} download>
          Download your {name}
        </a>
        {build?.saved === false ? (
          <span className="small muted">
            Not kept: your storage is full. <a href="#/documents">Make room</a>
          </span>
        ) : (
          <button type="button" className="linklike small" onClick={() => void start()}>Make it again</button>
        )}
      </span>
    );
  }
  return (
    <span className="make">
      {/* The CV is the one most people need, so the cover letter is the quieter of the two. */}
      <button type="button" disabled={making} onClick={() => void start()}
              className={[making && "making", kind === "cover" && "secondary"].filter(Boolean).join(" ")}>
        {making ? `Making your ${name}…` : `Make my ${name}`}
      </button>
      <Error>{error}</Error>
      {needsConsent ? <a className="small" href="#/privacy">Read what is sent, and agree</a> : null}
    </span>
  );
}

/** "€60–80k a year". */
function money(salary: NonNullable<Job["salary"]>): string {
  const symbol = { EUR: "€", USD: "$", GBP: "£" }[salary.currency] ?? `${salary.currency} `;
  const short = (value: number) => (value >= 10_000 ? `${Math.round(value / 1000)}k` : `${value}`);
  const range = salary.max !== salary.min ? `${short(salary.min)}–${short(salary.max)}` : short(salary.min);
  const period = { year: " a year", month: " a month", day: " a day", hour: " an hour" }[salary.period] ?? "";
  return `${symbol}${range}${period}`;
}
