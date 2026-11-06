/** Jobs, the home page: the jobs that match your words, each one a tap from a CV and a cover letter
 * made for it, and a tap on it shows all of it. New ones are announced, never pushed in under the
 * reader. Every alert lands here, on `#/feed?job=<id>`. */
import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";

import { api, type Ready, type Schema } from "../api";
import { useBuild } from "../documents";
import { JobView } from "../jobview";
import { keep, peek } from "../kept";
import { type Mark, mark as record, send, unsent } from "../marks";
import { POLL_MS } from "../notifications";
import { ago, useNow } from "../time";
import { Banner, Error, Prompt, useAction } from "../ui";

type Feed = Schema<"Feed">;
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

/** A board's name as people write it; anything not listed is its own name, capitalised. */
const BOARDS: Record<string, string> = {
  linkedin: "LinkedIn", hiringcafe: "Hiring Cafe", justjoinit: "Just Join IT", nofluffjobs: "No Fluff Jobs",
  workingnomads: "Working Nomads", themuse: "The Muse", smartrecruiters: "SmartRecruiters",
  link: "Added by link",
};
const board = (name: string) => BOARDS[name] ?? name.charAt(0).toUpperCase() + name.slice(1);

/** The orders the list comes in: its words, when the board says it was posted, when it arrived here. */
const ORDERS = [["best", "Most of your words first"], ["newest", "Newest posted first"],
                ["arrived", "Just arrived first"]] as const;
type Order = (typeof ORDERS)[number][0];

/** A country's name from its code, as the browser knows it in English. */
const REGIONS = new Intl.DisplayNames(["en"], { type: "region" });

/** The list as it first opens, kept (`kept.ts`): coming back to Jobs shows it at once. */
export const FEED = "/feed?offset=0";

/** Jobs has two tabs: the jobs that match your words, and one brought in by its link. */
export function JobsTabs({ on }: { on: "list" | "add" }) {
  return (
    <nav className="tabs" aria-label="Jobs">
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
  /** "I applied" changed the count the dashboard keeps. */
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState(false);
  const [since, setSince] = useState<number | null>(null);
  const [source, setSource] = useState("");
  const [country, setCountry] = useState("");
  const [city, setCity] = useState("");
  const [order, setOrder] = useState<Order>("best");
  const [noCv, setNoCv] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [atLeast, setAtLeast] = useState(1);
  const cached = peek<Feed>(FEED);
  // The boards and words their jobs have, kept from the first answer: a filtered one still offers them all.
  const [boards, setBoards] = useState<Feed["sources"]>(cached?.sources ?? []);
  const [words, setWords] = useState<Feed["words"]>(cached?.words ?? []);
  const [countries, setCountries] = useState<Feed["countries"]>(cached?.countries ?? []);
  const [cities, setCities] = useState<Feed["cities"]>(cached?.cities ?? []);
  const [feed, setFeed] = useState<Feed | null>(cached);
  const [failed, setFailed] = useState(false);
  // Skipped or applied here, by what: still counted in `feed.total`, so the heading subtracts them.
  // A skip, asked about first, is gone at once; "I applied" keeps an Undo for a moment.
  const [marked, setMarked] = useState<Map<number, Mark>>(new Map());
  const [gone, setGone] = useState<Set<number>>(new Set());
  // How many jobs were found since this list was read, and when the one before it was read: what
  // the "new jobs" button says, and which cards it marks new.
  const [fresh, setFresh] = useState(0);
  const [seenUntil, setSeenUntil] = useState<string | null>(null);
  const shown = (id: number) => !gone.has(id);
  const filtered = query.trim() !== "" || remote || since !== null || source !== "" || country !== ""
    || city !== "" || noCv || picked.length > 0 || atLeast > 1;
  const clear = () => {
    setQuery("");
    setRemote(false);
    setSince(null);
    setSource("");
    setCountry("");
    setCity("");
    setNoCv(false);
    setPicked([]);
    setAtLeast(1);
  };

  // A page is as long as the server's own page (`FEED_PAGE`): the next one starts where this one ends.
  const read = useCallback(async (offset: number, foundAfter?: string): Promise<Feed> => {
    const params = new URLSearchParams({ offset: `${offset}` });
    if (query.trim()) params.set("q", query.trim());
    if (remote) params.set("remote", "true");
    if (since) params.set("since_hours", `${since}`);
    if (source) params.set("source", source);
    if (country) params.set("country", country);
    if (city) params.set("city", city);
    if (order !== "best") params.set("sort", order);
    if (noCv) params.set("made", "false");
    for (const word of picked) params.append("word", word);
    if (atLeast > 1) params.set("min_words", `${atLeast}`);
    if (foundAfter) {
      params.set("found_after", foundAfter);
      params.set("limit", "1");
    }
    return api.get<Feed>(`/feed?${params}`);
  }, [query, remote, since, source, country, city, order, noCv, picked, atLeast]);

  const reload = useCallback(() => {
    // Marks this browser still holds go first, so the answer already leaves them out.
    void send().finally(() => read(0).then((body) => {
      if (!filtered && order === "best") keep(FEED, body);
      setBoards(body.sources);
      setWords(body.words);
      setCountries(body.countries);
      setCities(body.cities);
      setFeed(body);
      setMarked(new Map());
      setGone(unsent());
      setFresh(0);
      setFailed(false);
    }).catch(() => setFailed(true)));
  }, [read, filtered, order]);

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
      if (document.visibilityState === "visible") read(0, asOf).then((body) => setFresh(body.total)).catch(() => undefined);
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
  // The AI versions only when a build would be allowed: someone can pay, and there is a profile.
  const ai = dashboard.ai.payer !== null && dashboard.ai.profile;
  const card = (job: Job, isFocus = false) => (
    <JobCard key={job.id} job={job} ai={ai} focus={isFocus} done={marked.get(job.id)} onMark={onMark}
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
          {fresh} new {fresh === 1 ? "job" : "jobs"} · Show {fresh === 1 ? "it" : "them"}
        </button>
      ) : null}

      <p className="kicker">Your jobs</p>
      <h1>
        {feed === null
          ? "Finding your jobs…"
          : `${total} ${total === 1 ? "job matches" : "jobs match"} your ${filtered ? "search" : "words"}`}
      </h1>
      {dashboard.applied ? (
        <p className="small muted">
          You have applied to {dashboard.applied} {dashboard.applied === 1 ? "job" : "jobs"}.{" "}
          <a href="#/applied">See them</a>
        </p>
      ) : null}

      <div className="toolbar">
        <input type="search" placeholder="Search your jobs" aria-label="Search your jobs" value={query}
               onChange={(event) => setQuery(event.target.value)} />
      </div>
      <div className="filters">
        <select aria-label="Posted" value={since ?? ""}
                onChange={(event) => setSince(event.target.value ? Number(event.target.value) : null)}>
          {POSTED.map(([label, hours]) => <option key={label} value={hours ?? ""}>{label}</option>)}
        </select>
        <select aria-label="Job board" value={source} onChange={(event) => setSource(event.target.value)}>
          <option value="">All boards</option>
          {boards.map((each) => (
            <option key={each.name} value={each.name}>{board(each.name)} ({each.jobs})</option>
          ))}
        </select>
        {countries.length > 1 || country ? (
          <select aria-label="Country" value={country} onChange={(event) => setCountry(event.target.value)}>
            <option value="">All countries</option>
            {countries.map((each) => (
              <option key={each.value} value={each.value}>{each.label} ({each.jobs})</option>
            ))}
          </select>
        ) : null}
        {cities.length > 1 || city ? (
          <select aria-label="City" value={city} onChange={(event) => setCity(event.target.value)}>
            <option value="">All cities</option>
            {cities.map((each) => <option key={each.value} value={each.value}>{each.label} ({each.jobs})</option>)}
          </select>
        ) : null}
        <select aria-label="Order" value={order} onChange={(event) => setOrder(event.target.value as Order)}>
          {ORDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        {words.length > 1 ? (
          <select aria-label="How many of your words" value={atLeast}
                  onChange={(event) => setAtLeast(Number(event.target.value))}>
            <option value={1}>Any word</option>
            {[2, 3, 4].filter((count) => count <= words.length).map((count) => (
              <option key={count} value={count}>{count}+ words</option>
            ))}
          </select>
        ) : null}
        {([["Remote only", remote, setRemote], ["No CV yet", noCv, setNoCv]] as const).map(([label, on, set]) => (
          <button key={label} type="button" className={on ? "chip on" : "chip"} aria-pressed={on}
                  onClick={() => set(!on)}>
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
                      onClick={() => setPicked(on ? picked.filter((each) => each !== word.name) : [...picked, word.name])}>
                {word.name} <span className="count">{word.jobs}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      {focus !== null && shown(focus) ? (
        <Focused id={focus} ai={ai} card={(job) => card(job, true)} />
      ) : null}

      {failed ? <p className="error">Your jobs did not load. Refresh the page to try again.</p> : null}
      {feed && feed.jobs.length === 0 ? (
        <p className="muted">
          {filtered ? "Nothing matches these filters." : (
            <>
              No jobs match your words yet. New jobs come in all day, or{" "}
              <a href="#/words">add more words</a>.
            </>
          )}
        </p>
      ) : null}
      <div className="jobs">
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
function Focused({ id, ai, card }: { id: number; ai: boolean; card: (job: Job) => ReactNode }) {
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
      <p className="small muted">You can still make your documents for it.</p>
      <div className="job-actions">
        <MakeDocument doc={cv} kind="cv" ai={ai} />
        <MakeDocument doc={cover} kind="cover" ai={ai} />
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

export function JobCard({ job, ai, focus, done, fresh, onMark }: {
  job: Job;
  ai: boolean;
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
        <span className="muted small">Applied: {job.title}</span>
        <button type="button" className="linklike" onClick={() => onMark(job, done, false)}>Undo</button>
      </article>
    );
  }

  const existing = latest(job.documents);
  const documents = (
    <div className="job-actions">
      <MakeDocument doc={cv} kind="cv" ai={ai} existing={existing.cv} />
      <MakeDocument doc={cover} kind="cover" ai={ai} existing={existing.cover} />
    </div>
  );
  const marks = (
    <div className="job-actions">
      <a className="button secondary small" href={job.url} target="_blank" rel="noreferrer">
        {job.source === "link" || job.source === "typed" ? "Open the job" : `Open on ${board(job.source)}`}
      </a>
      <button type="button" className="secondary small" onClick={() => setSkipping(true)}>Skip</button>
      <button type="button" className="secondary small" onClick={() => onMark(job, "applied", true)}>
        I applied
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
function MakeDocument({ doc, kind, ai, existing }: { doc: Build; kind: "cv" | "cover"; ai: boolean; existing?: Made }) {
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
      {ai && !making ? (
        <button type="button" className="linklike small" onClick={() => void start("ai")}>
          or have AI write it
        </button>
      ) : null}
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
