/** Jobs, the home page: how many match your words, words worth adding, and the jobs, each one a tap
 * from a CV and a cover letter made for it. Every alert lands here, on `#/feed?job=<id>`. */
import { useCallback, useEffect, useState } from "react";

import { api, type Ready, type Schema } from "../api";
import { useBuild } from "../documents";
import { ago } from "../time";
import { Banner, Error, Suggested, useAction } from "../ui";

type Feed = Schema<"Feed">;
type Job = Schema<"FeedJob">;
type Made = Schema<"JobDocument">;

/** The newest of each kind, which is the one a card offers. */
export function latest(documents: Made[]): Partial<Record<Made["kind"], Made>> {
  const found: Partial<Record<Made["kind"], Made>> = {};
  for (const each of documents) found[each.kind] ??= each;
  return found;
}
type Suggestions = Schema<"WordSuggestions">;

/** How long a hidden job keeps its Undo before it leaves the page. */
const UNDO_MS = 6000;

/** "Posted": hours back, as the server's `since_hours` takes them. */
const POSTED: [string, number | null][] = [["Any time", null], ["Last 24 hours", 24], ["Last 3 days", 72],
                                           ["Last week", 168]];

/** A board's name as people write it; anything not listed is its own name, capitalised. */
const BOARDS: Record<string, string> = {
  linkedin: "LinkedIn", hiringcafe: "Hiring Cafe", justjoinit: "Just Join IT", nofluffjobs: "No Fluff Jobs",
  workingnomads: "Working Nomads", themuse: "The Muse", smartrecruiters: "SmartRecruiters",
};
const board = (name: string) => BOARDS[name] ?? name.charAt(0).toUpperCase() + name.slice(1);

// The last answer, kept across mounts: coming back to Jobs shows the list at once.
let cached: Feed | null = null;

/** Forgotten on sign-out, or the next account flashes this one's jobs. */
export function clearJobsCache(): void {
  cached = null;
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
  const [newest, setNewest] = useState(false);
  const [notApplied, setNotApplied] = useState(false);
  const [noCv, setNoCv] = useState(false);
  // The boards their jobs come from, kept from the first answer: a filtered answer still lists them all.
  const [boards, setBoards] = useState<Feed["sources"]>(cached?.sources ?? []);
  const [feed, setFeed] = useState<Feed | null>(cached);
  const [failed, setFailed] = useState(false);
  // Hidden on the server but still counted in `feed.total`, so the heading subtracts them. Each keeps
  // an Undo for a moment, then is gone: held here, so a card shown again cannot forget it.
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [gone, setGone] = useState<Set<number>>(new Set());
  const shown = (id: number) => !(hidden.has(id) && gone.has(id));
  const filtered = query.trim() !== "" || remote || since !== null || source !== "" || notApplied || noCv;
  const clear = () => {
    setQuery("");
    setRemote(false);
    setSince(null);
    setSource("");
    setNotApplied(false);
    setNoCv(false);
  };

  // A page is as long as the server's own page (`FEED_PAGE`): the next one starts where this one ends.
  const read = useCallback(async (offset: number): Promise<Feed> => {
    const params = new URLSearchParams({ offset: `${offset}` });
    if (query.trim()) params.set("q", query.trim());
    if (remote) params.set("remote", "true");
    if (since) params.set("since_hours", `${since}`);
    if (source) params.set("source", source);
    if (newest) params.set("sort", "newest");
    if (notApplied) params.set("applied", "false");
    if (noCv) params.set("made", "false");
    return api.get<Feed>(`/feed?${params}`);
  }, [query, remote, since, source, newest, notApplied, noCv]);

  const reload = useCallback(() => {
    read(0).then((body) => {
      if (!filtered && !newest) cached = body;
      setBoards(body.sources);
      setFeed(body);
      setHidden(new Set());
      setGone(new Set());
      setFailed(false);
    }).catch(() => setFailed(true));
  }, [read, filtered, newest]);

  // Typing does not fire a request per key.
  useEffect(() => {
    const timer = window.setTimeout(reload, query ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [reload, query]);

  // Hidden jobs have left the server's list, so the next page starts that many earlier, and its
  // total no longer counts them while this one does.
  const more = useAction(async () => {
    if (!feed) return;
    const next = await read(feed.jobs.filter((job) => !hidden.has(job.id)).length);
    const have = new Set(feed.jobs.map((job) => job.id));
    setFeed({ total: next.total + hidden.size, sources: next.sources,
              jobs: [...feed.jobs, ...next.jobs.filter((job) => !have.has(job.id))] });
  });

  const marked = (id: number, isHidden: boolean) => {
    setHidden((now) => toggled(now, id, isHidden));
    setGone((now) => toggled(now, id, false));
    if (isHidden) window.setTimeout(() => setGone((now) => toggled(now, id, true)), UNDO_MS);
  };

  const total = feed ? feed.total - hidden.size : 0;
  // The AI versions only when a build would be allowed: someone can pay, and there is a profile.
  const ai = dashboard.ai.payer !== null && dashboard.ai.profile;
  return (
    <>
      {dashboard.banners.map((banner) => (
        <Banner key={banner.key} level={banner.level}>
          <span>{banner.text}</span>{" "}
          <a href="#/settings">
            {banner.key === "telegram_broken" ? "Fix it in Settings"
              : banner.key === "paused" ? "Turn them back on" : "Turn on alerts"}
          </a>
        </Banner>
      ))}

      <p className="kicker">Your jobs</p>
      <h1>
        {feed === null
          ? "Finding your jobs…"
          : `${total} ${total === 1 ? "job matches" : "jobs match"} your ${filtered ? "search" : "words"}`}
      </h1>
      {dashboard.applied ? (
        <p className="small muted">
          You have applied to {dashboard.applied} {dashboard.applied === 1 ? "job" : "jobs"}.
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
        <select aria-label="Order" value={newest ? "newest" : "best"}
                onChange={(event) => setNewest(event.target.value === "newest")}>
          <option value="best">Best match first</option>
          <option value="newest">Newest first</option>
        </select>
        {([["Remote only", remote, setRemote], ["Not applied yet", notApplied, setNotApplied],
           ["No CV yet", noCv, setNoCv]] as const).map(([label, on, set]) => (
          <button key={label} type="button" className={on ? "chip on" : "chip"} aria-pressed={on}
                  onClick={() => set(!on)}>
            {label}
          </button>
        ))}
        {filtered ? <button type="button" className="linklike small" onClick={clear}>Clear filters</button> : null}
      </div>

      <NewWords onAccepted={reload} />

      {focus !== null && shown(focus) ? (
        <Focused id={focus} ai={ai} hidden={hidden.has(focus)} onMarked={marked} onApplied={onChanged} />
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
        {feed?.jobs.filter((job) => job.id !== focus && shown(job.id)).map((job) => (
          <JobCard key={job.id} job={job} ai={ai} hidden={hidden.has(job.id)} onMarked={marked}
                   onApplied={onChanged} />
        ))}
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
function Focused({ id, ai, hidden, onMarked, onApplied }: {
  id: number;
  ai: boolean;
  hidden: boolean;
  onMarked: (id: number, hidden: boolean) => void;
  onApplied: () => void;
}) {
  const [job, setJob] = useState<Job | null | undefined>(undefined);
  useEffect(() => {
    setJob(undefined);
    api.get<Feed>(`/feed?job=${id}`).then((body) => setJob(body.jobs[0] ?? null)).catch(() => setJob(null));
  }, [id]);

  if (job === undefined) return null;
  if (job) return <JobCard job={job} ai={ai} hidden={hidden} focus onMarked={onMarked} onApplied={onApplied} />;
  return (
    <article className="job-card focus">
      <p>This job is not in your list any more. You may have hidden it, or it is too old.</p>
      <p className="small muted">You can still make your documents for it.</p>
      <div className="job-actions">
        <MakeDocument jobId={id} kind="cv" ai={ai} />
        <MakeDocument jobId={id} kind="cover" ai={ai} />
      </div>
    </article>
  );
}

/** Words their documents suggest, one tap to take or to say no to. */
function NewWords({ onAccepted }: { onAccepted: () => void }) {
  const [words, setWords] = useState<Suggestions | null>(null);
  useEffect(() => {
    api.get<Suggestions>("/suggestions").then(setWords).catch(() => undefined);
  }, []);
  const take = useAction(async (term: string, kind: "green" | "red") => {
    setWords(await api.post<Suggestions>("/suggestions/accept", { term, kind }));
    onAccepted();
  });
  const dismiss = useAction(async (term: string) => {
    setWords(await api.post<Suggestions>("/suggestions/dismiss", { term }));
  });

  if (!words || words.green.length + words.red.length === 0) return null;
  return (
    <section className="new-words">
      <p className="small muted">New words for you: + shows more jobs with it, − hides jobs with it.</p>
      <ul className="suggestions">
        {(["green", "red"] as const).flatMap((tone) =>
          words[tone].map((word) => (
            <Suggested key={tone + word.term} tone={tone} term={word.term} reason={word.reason}
                       onTake={() => void take.run(word.term, tone)}
                       onDismiss={() => void dismiss.run(word.term)} />
          )),
        )}
      </ul>
      <Error>{take.error ?? dismiss.error}</Error>
    </section>
  );
}

function JobCard({ job, ai, hidden, focus = false, onMarked, onApplied }: {
  job: Job;
  ai: boolean;
  hidden: boolean;
  focus?: boolean;
  onMarked: (id: number, hidden: boolean) => void;
  onApplied: () => void;
}) {
  const [applied, setApplied] = useState(job.applied_at !== null);
  // Made before (kept, so a refresh still shows it) or made now: what colours the card.
  const [made, setMade] = useState<Set<Made["kind"]>>(() => new Set(job.documents.map((each) => each.kind)));
  const existing = latest(job.documents);
  // The whole description, fetched the first time it is asked for; `null` while folded.
  const [full, setFull] = useState<string | null>(null);
  const expand = useAction(async () => {
    setFull((await api.get<Schema<"JobDescription">>(`/jobs/${job.id}/description`)).text);
  });
  const madeNow = useCallback((kind: Made["kind"]) =>
    setMade((now) => (now.has(kind) ? now : new Set([...now, kind]))), []);
  // PUT records "I applied" or "Hide", DELETE takes it back.
  const mark = useAction(async (name: "applied" | "hidden", on: boolean) => {
    const path = `/jobs/${job.id}/${name}`;
    const now = await (on ? api.put<Schema<"JobMarks">>(path) : api.del<Schema<"JobMarks">>(path));
    setApplied(now.applied);
    onMarked(job.id, now.hidden);
    if (name === "applied") onApplied();
  });

  if (hidden) {
    return (
      <article className="job-card gone">
        <span className="muted small">Hidden: {job.title}</span>
        <button type="button" className="linklike" disabled={mark.busy}
                onClick={() => void mark.run("hidden", false)}>
          Undo
        </button>
      </article>
    );
  }

  const place = job.location && /remote/i.test(job.location) ? job.location
    : [job.location, job.is_remote ? "Remote" : null].filter(Boolean).join(" · ");
  const age = ago(job.posted_at ?? job.first_seen_at);
  const when = age ? `${job.posted_at ? "Posted" : "Found"} ${age}` : null;
  return (
    <article className={["job-card", focus && "focus", made.has("cv") && "made-cv", made.has("cover") && "made-cover",
                         full !== null && "open"].filter(Boolean).join(" ")}>
      {made.size ? (
        <p className="made-badges">
          {made.has("cv") ? <span className="made-badge cv">CV made</span> : null}
          {made.has("cover") ? <span className="made-badge cover">Cover letter made</span> : null}
        </p>
      ) : null}
      <h3>
        <a href={job.url} target="_blank" rel="noreferrer">{job.title}</a>
      </h3>
      <p className="job-meta">{[job.company, place, when].filter(Boolean).join(" · ")}</p>
      {job.salary ? <p className="job-meta">{money(job.salary)}</p> : null}
      {full !== null ? (
        <div className="description">
          <p>{full}</p>
          <button type="button" className="linklike small" onClick={() => setFull(null)}>Show less</button>
        </div>
      ) : job.summary ? (
        <p className="summary">
          {job.summary}{" "}
          {job.more ? (
            <button type="button" className="linklike small" disabled={expand.busy} onClick={() => void expand.run()}>
              {expand.busy ? "Loading…" : "Show the full description"}
            </button>
          ) : null}
        </p>
      ) : null}
      <Error>{expand.error}</Error>
      <p className="why">
        Why:{" "}
        {job.words.map((word) => (
          <span key={word} className="word green">{word}</span>
        ))}
      </p>
      <div className="job-actions">
        <MakeDocument jobId={job.id} kind="cv" ai={ai} existing={existing.cv} onMade={madeNow} />
        <MakeDocument jobId={job.id} kind="cover" ai={ai} existing={existing.cover} onMade={madeNow} />
      </div>
      <div className="job-actions">
        <a className="button secondary small" href={job.url} target="_blank" rel="noreferrer">Open job</a>
        <button type="button" className="secondary small" disabled={mark.busy}
                onClick={() => void mark.run("hidden", true)}>
          Hide
        </button>
        <button type="button" className={applied ? "secondary small on" : "secondary small"}
                aria-pressed={applied} disabled={mark.busy} onClick={() => void mark.run("applied", !applied)}>
          {applied ? "I applied ✓" : "I applied"}
        </button>
      </div>
      <Error>{mark.error}</Error>
    </article>
  );
}

/** One document for one job: a button, then the words while it is made, then the download — and
 * the download straight away for one made before and kept. */
function MakeDocument({ jobId, kind, ai, existing, onMade }: {
  jobId: number;
  kind: "cv" | "cover";
  ai: boolean;
  existing?: Made;
  onMade?: (kind: Made["kind"]) => void;
}) {
  const { build, making, start, error, needsConsent } = useBuild(jobId, kind);
  const name = kind === "cv" ? "CV" : "cover letter";
  const ready = build?.state === "ready" && build.download_url ? build.download_url : null;
  useEffect(() => {
    if (ready) onMade?.(kind);
  }, [ready, kind, onMade]);
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

function toggled(set: Set<number>, id: number, on: boolean): Set<number> {
  const next = new Set(set);
  if (on) next.add(id);
  else next.delete(id);
  return next;
}

/** "€60–80k a year". */
function money(salary: NonNullable<Job["salary"]>): string {
  const symbol = { EUR: "€", USD: "$", GBP: "£" }[salary.currency] ?? `${salary.currency} `;
  const short = (value: number) => (value >= 10_000 ? `${Math.round(value / 1000)}k` : `${value}`);
  const range = salary.max !== salary.min ? `${short(salary.min)}–${short(salary.max)}` : short(salary.min);
  const period = { year: " a year", month: " a month", day: " a day", hour: " an hour" }[salary.period] ?? "";
  return `${symbol}${range}${period}`;
}
