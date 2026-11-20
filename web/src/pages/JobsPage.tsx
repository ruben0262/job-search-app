/** Jobs, the home page: the jobs that match your words, each a tap from a CV and a cover letter written
 * for it with their own AI account, and a tap on one shows all of it. New ones are announced, never
 * pushed in under the reader. Every alert lands here, on `#/feed?job=<id>`. */
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent, ReactNode } from "react";

import { api, request, type Ready, type Schema } from "../api";
import { useBuild } from "../documents";
import { JobView } from "../jobview";
import { keep, peek, useKept } from "../kept";
import { type Mark, bookmark, bookmarked, mark as record, send, unsent } from "../marks";
import { POLL_MS } from "../notifications";
import { PLACES } from "../places";
import { ago, span, useNow } from "../time";
import { Banner, Error, Field, Prompt, Sheet, useAction } from "../ui";

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
                                           ["Last week", 168], ["Last 2 weeks", 336]];

/** The pictures on a job's own buttons, in each button's colour: out to the site it was posted on, the tick
 * that is Applied's picture, an eye shut on a job not shown again, and the bookmark in a card's corner. The
 * landing page's card has them too. */
export const PICTURES = {
  apply: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13.5 4.5h6v6M19.5 4.5l-8 8M18 13.5v5a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 014 18.5v-11A1.5 1.5 0 015.5 6h5" /></svg>,
  applied: <svg viewBox="0 0 24 24" aria-hidden="true">{PLACES.find(([key]) => key === "applied")?.[2]}</svg>,
  skip: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM14.5 12a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0zM4 4l16 16" /></svg>,
  bookmark: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h10a1 1 0 011 1v15l-6-4-6 4V5a1 1 0 011-1z" /></svg>,
};

/** The orders the list comes in: its words, when the board says it was posted, when it arrived here. */
const ORDERS = [["best", "Most of your words first"], ["newest", "Newest posted first"],
                ["arrived", "Just arrived first"]] as const;

/** The other pictures the job lists use: the sliders on the button that opens the filters, the arrow back of an
 * Undo, the tray of a download and the circling arrow of "Make it again". Applied has them too. */
export const MORE_PICTURES = {
  filters: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h9M17 7h3M4 17h3M11 17h9M15 4.5v5M9 14.5v5" /></svg>,
  undo: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14L4.5 9.5 9 5M4.5 9.5H15a4.5 4.5 0 010 9h-3" /></svg>,
  download: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14" /></svg>,
  again: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.5 12a7.5 7.5 0 11-2.2-5.3M19.5 4.5v4.2h-4.2" /></svg>,
};

/** A country's name from its code, as the browser knows it in English. */
const REGIONS = new Intl.DisplayNames(["en"], { type: "region" });

/** The list as it first opens, kept (`kept.ts`): coming back to Jobs shows it at once. */
/** The filters as this tab last had them (`kept.ts`), over the saved ones the dashboard brought. */
const FILTERS = "/feed/filters";

/** The feed's question for these filters: each one that is on, by the name `/feed` takes it. Its answer is
 * kept under it, so the page comes back drawn at once, whatever they had chosen. */
function asked(filters: Filters, offset = 0): URLSearchParams {
  const { q, remote, since_hours, source, country, city, sort, made, word = [], min_words, min_fit } = filters;
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
  if (min_fit) params.set("min_fit", `${min_fit}`);
  return params;
}

/** The scores the match filter offers, each "this or more": below 5 is not a fit worth asking for. */
const FIT_STEPS = [5, 6, 7, 8, 9];

/** Each filter that narrows the list past their words, as the chip under the search says it, with the change
 * that takes it off: what the button to the filters counts, and "Clear all" takes off together. */
function narrowing(filters: Filters, known: {
  boards: Feed["sources"]; countries: Feed["countries"]; cities: Feed["cities"]; scoring: boolean;
}): [string, Partial<Filters>][] {
  const { remote, source, country, city, made, word = [], min_words: atLeast, min_fit: fit } = filters;
  const named = (options: { value: string; label: string }[], value: string) =>
    options.find((each) => each.value === value)?.label ?? value;
  const on: [string, Partial<Filters>][] = [];
  if (known.scoring && fit) on.push([`Match ${fit}+`, { min_fit: null }]);
  if (country) on.push([named(known.countries, country), { country: "" }]);
  if (city) on.push([named(known.cities, city), { city: "" }]);
  if (remote) on.push(["Remote only", { remote: false }]);
  for (const each of word) on.push([each, { word: word.filter((other) => other !== each) }]);
  if (atLeast > 1) on.push([`${atLeast}+ of your words`, { min_words: 1 }]);
  if (source) on.push([known.boards.find((each) => each.name === source)?.label ?? source, { source: "" }]);
  if (made === false) on.push(["No CV yet", { made: null }]);
  return on;
}

/** Jobs has three tabs: the jobs that match your words, the ones you bookmarked, and one brought in by
 * its link. */
const TABS = [["list", "#/", "Your jobs"], ["bookmarks", "#/bookmarks", "Bookmarks"],
              ["add", "#/add", "Add a job"]] as const;

export function JobsTabs({ on }: { on: (typeof TABS)[number][0] }) {
  return (
    <nav className="tabs split" aria-label="Jobs">
      {TABS.map(([key, href, name]) => (
        <a key={key} className={on === key ? "tab on" : "tab"} href={href}
           aria-current={on === key ? "page" : undefined}>
          {name}
        </a>
      ))}
    </nav>
  );
}

/** The jobs kept under `path` (`kept.ts`), as cards, with what is done to each: a skipped one is gone,
 * one marked applied shows its Undo. What Bookmarks and Add a job both list. Marks still waiting in this
 * browser are sent before the list is asked for, so a bookmark tapped a moment ago is in it. */
export function useJobList(path: string, onChanged: () => void) {
  const { data: jobs, set, failed } = useKept<Job[]>(path, send);
  const [marked, setMarked] = useState<Map<number, Mark>>(new Map());
  const onMark = (job: Job, kind: Mark, on: boolean) => {
    setMarked((now) => {
      const next = new Map(now);
      if (on) next.set(job.id, kind);
      else next.delete(job.id);
      return next;
    });
    // Gone from the list kept for next time at once; this one keeps its Undo while it is open.
    keep(path, on ? (jobs ?? []).filter((each) => each.id !== job.id) : jobs);
    void record(job.id, kind, on).then(() => {
      if (kind === "applied") onChanged();
    });
  };
  const card = (job: Job) => marked.get(job.id) === "hidden" ? null : (
    <JobCard key={job.id} job={job} focus={false} fresh={false} onMark={onMark}
             done={marked.get(job.id) === "applied" ? "applied" : undefined} />
  );
  return { jobs, set, failed, card };
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
  const { q: query, since_hours: since } = filters;
  // The filters, open over the page: the list behind them follows each change as it is made.
  const [choosing, setChoosing] = useState(false);
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
  // Their plan has match scores and they have documents to compare with: the filter by it is offered.
  const scoring = feed?.match_scores ?? false;
  // Narrowed past their words, each said as a chip under the search that takes it off, and the search itself.
  // The time chosen under "Posted" and the order are how the list is shown, not what is in it, so they stay
  // when the rest is cleared.
  const on = narrowing(filters, { boards, countries, cities, scoring });
  const filtered = query.trim() !== "" || on.length > 0;
  const clear = () => set({ q: "", remote: false, source: "", country: "", city: "", made: null, word: [],
                            min_words: 1, min_fit: null });

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
      // How many there are now and how many are new, and nothing else: no job comes back with it.
      params.set("found_after", foundAfter);
      params.set("counts", "true");
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
  // How long the longest-kept jobs stay, once an answer says: "Any time" under "Posted" is all of it. And how
  // old a job is before their plan shows it: a shorter time would be empty, so the server reads it as all
  // of their list (`/feed`), and so does "Posted" here.
  const longest = feed?.shelf_longest_hours ?? Infinity;
  const delay = feed?.delay_hours ?? 0;
  const within = since && since > delay && since < longest ? since : null;
  // What "Posted" is set to, in a sentence: "the last 24 hours".
  const posted = within
    ? (POSTED.find(([, hours]) => hours === within)?.[0] ?? `Last ${span(within)}`).toLowerCase() : null;
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
          <a className="button small" href={banner.key === "free" ? "#/upgrade" : "#/alerts"}>
            {banner.key === "free" ? "Become a member" : "Fix it in Alerts"}
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
          {delay ? `You see new jobs after ${span(delay)}. ` : ""}Jobs stay here for {span(feed.shelf_hours)}
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

      {/* The search and how far back, at hand; every other filter behind one button, and each one that is on
          said under them as a chip that takes it off (owner, 2026-11-16: "simplify it ... not clunky"). */}
      <div className="finder">
        <input type="search" placeholder="Search your jobs" aria-label="Search your jobs" value={query}
               onChange={(event) => set({ q: event.target.value })} />
        <label className="finder-posted">
          <span>Posted</span>
          {/* Only the times shorter than jobs stay and longer than their plan waits: "Any time" is all of them. */}
          <select aria-label="Posted" value={within ?? ""}
                  onChange={(event) => set({ since_hours: event.target.value ? Number(event.target.value) : null })}>
            {POSTED.filter(([, hours]) => hours === null || (hours > delay && hours < longest)).map(([label, hours]) => (
              <option key={label} value={hours ?? ""}>{label}</option>
            ))}
          </select>
        </label>
        <button type="button" className="secondary finder-open" aria-haspopup="dialog"
                aria-label={on.length ? `Filters, ${on.length} on` : undefined} onClick={() => setChoosing(true)}>
          {MORE_PICTURES.filters}Filters{on.length ? <span className="finder-count">{on.length}</span> : null}
        </button>
      </div>
      {on.length ? (
        <div className="filters-on" role="group" aria-label="Filters on">
          {on.map(([label, off], i) => (
            <button key={`${i}:${label}`} type="button" className="chip on" aria-label={`Take off: ${label}`}
                    onClick={() => set(off)}>
              {label}<span className="chip-x" aria-hidden="true">×</span>
            </button>
          ))}
          <button type="button" className="chip clear" onClick={clear}>Clear all</button>
        </div>
      ) : null}
      {choosing ? (
        <FilterSheet filters={filters} set={set} clear={clear} on={on.length > 0} scoring={scoring}
                     boards={boards} words={words} countries={countries} cities={cities}
                     onClose={() => setChoosing(false)}
                     showing={busy || !feed ? "Finding your jobs…"
                       : `Show ${total}${feed.cut ? "+" : ""} ${total === 1 && !feed.cut ? "job" : "jobs"}`} />
      ) : null}

      {focus !== null && shown(focus) ? (
        <Focused id={focus} card={(job) => card(job, true)} />
      ) : null}

      {failed ? <p className="error">Your jobs did not load. Refresh the page to try again.</p> : null}
      {feed && feed.jobs.length === 0 ? (
        <p className="muted">
          {filtered ? (
            <>
              Nothing matches these filters.{" "}
              <button type="button" className="secondary small" onClick={clear}>Clear filters</button>
            </>
          ) : posted ? (
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

/** The filters, over the page (owner, 2026-11-16: "lots to see and navigate"): each kind under a heading that
 * says what it is, the list behind following every change as it is made, and one button back to it that says
 * how many jobs it holds now. */
function FilterSheet({ filters, set, clear, on, scoring, boards, words, countries, cities, showing, onClose }: {
  filters: Filters;
  set: (change: Partial<Filters>) => void;
  clear: () => void;
  /** Any filter is on: "Clear all" is offered. */
  on: boolean;
  scoring: boolean;
  boards: Feed["sources"];
  words: Feed["words"];
  countries: Feed["countries"];
  cities: Feed["cities"];
  /** What the button back to the list says: "Show 332 jobs". */
  showing: string;
  onClose: () => void;
}) {
  const { remote, source, country, city, sort: order, made, word: picked = [], min_words: atLeast } = filters;
  const fit = scoring ? filters.min_fit ?? null : null;
  return (
    <Sheet title="Filters" onClose={onClose}>
      <div className="filter-sheet">
        {/* Numbers, not words (owner, 2026-11-16: "a score or above it"), each in its colour on the cards' line
            from red to green, the one picked filled. */}
        {scoring ? (
          <section className="filter-group">
            <h3>Match score</h3>
            <p className="filter-hint">How well a job fits your CV, out of 10. Pick the lowest you want to see.</p>
            <div className="score-steps" role="group" aria-label="Match score">
              {[null, ...FIT_STEPS].map((at) => (
                <button key={at ?? "any"} type="button" aria-pressed={fit === at}
                        aria-label={at === null ? "Any match score" : `Match score ${at} or more`}
                        className={`chip score-step${at === null ? " any" : ""}${fit === at ? " on" : ""}`}
                        style={at === null ? undefined : { "--fit": at } as CSSProperties}
                        onClick={() => set({ min_fit: at })}>
                  {at === null ? "Any" : `${at}+`}
                </button>
              ))}
            </div>
          </section>
        ) : null}
        <section className="filter-group">
          <h3>Where</h3>
          {countries.length > 1 || country || cities.length > 1 || city ? (
            <div className="filter-fields">
              {countries.length > 1 || country ? (
                <Field label="Country">
                  <select value={country} onChange={(event) => set({ country: event.target.value })}>
                    <option value="">All countries</option>
                    {countries.map((each) => (
                      <option key={each.value} value={each.value}>{each.label} ({each.jobs})</option>
                    ))}
                  </select>
                </Field>
              ) : null}
              {cities.length > 1 || city ? (
                <Field label="City">
                  <select value={city} onChange={(event) => set({ city: event.target.value })}>
                    <option value="">All cities</option>
                    {cities.map((each) => <option key={each.value} value={each.value}>{each.label} ({each.jobs})</option>)}
                  </select>
                </Field>
              ) : null}
            </div>
          ) : null}
          <label className="pick">
            <input type="checkbox" role="switch" checked={remote} onChange={() => set({ remote: !remote })} />
            <span>Remote jobs only</span>
          </label>
        </section>
        {words.length > 1 ? (
          <section className="filter-group">
            <h3>Your words</h3>
            <p className="filter-hint">Show only the jobs that have the words you tap.</p>
            <div className="chips" role="group" aria-label="Only jobs with">
              {words.map((each) => {
                const pressed = picked.includes(each.name);
                return (
                  <button key={each.name} type="button" className={pressed ? "chip on" : "chip"} aria-pressed={pressed}
                          onClick={() => set({ word: pressed ? picked.filter((other) => other !== each.name)
                            : [...picked, each.name] })}>
                    {each.name} <span className="count">{each.jobs}</span>
                  </button>
                );
              })}
            </div>
            <Field label="How many of your words a job needs">
              <select value={atLeast} onChange={(event) => set({ min_words: Number(event.target.value) })}>
                <option value={1}>Any one of them</option>
                {[2, 3, 4].filter((count) => count <= words.length).map((count) => (
                  <option key={count} value={count}>At least {count}</option>
                ))}
              </select>
            </Field>
          </section>
        ) : null}
        <section className="filter-group">
          <h3>More</h3>
          <Field label="Job site">
            <select value={source} onChange={(event) => set({ source: event.target.value })}>
              <option value="">Every job site</option>
              {boards.map((each) => <option key={each.name} value={each.name}>{each.label} ({each.jobs})</option>)}
            </select>
          </Field>
          <label className="pick">
            <input type="checkbox" role="switch" checked={made === false}
                   onChange={() => set({ made: made === false ? null : false })} />
            <span>Only jobs you have no CV for yet</span>
          </label>
        </section>
        <section className="filter-group">
          <h3>Sort by</h3>
          <div className="toggles" role="group" aria-label="Sort by">
            {ORDERS.map(([value, label]) => (
              <button key={value} type="button" className="toggle" aria-pressed={order === value}
                      onClick={() => set({ sort: value })}>
                {label}
              </button>
            ))}
          </div>
        </section>
      </div>
      <div className="filter-foot">
        {on ? <button type="button" className="secondary" onClick={clear}>Clear all</button> : null}
        <button type="button" onClick={onClose}>{showing}</button>
      </div>
    </Sheet>
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
      <div className="job-docs">
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
  // A little mark in the card's corner (owner, 2026-11-15): tapped on, the job is under Bookmarks too.
  const [saved, setSaved] = useState(() => bookmarked(job));
  const save = () => {
    setSaved(!saved);
    void bookmark(job.id, !saved);
  };
  // Made before (kept, so a refresh still shows it) or made now: what colours the card's edge, beside its
  // "Download CV", which says it in words.
  const made = new Set(job.documents.map((each) => each.kind));
  if (cv.build?.state === "ready") made.add("cv");
  if (cover.build?.state === "ready") made.add("cover");

  // Marked applied a moment ago: said in a line, with an Undo that is a button like any other (owner,
  // 2026-11-16: "when we undo its a writing or a hyperlink").
  if (done) {
    return (
      <article className="job-card gone">
        <span className="gone-mark">{PICTURES.applied}</span>
        <p><strong>Moved to Applied:</strong> {job.title}</p>
        <button type="button" className="secondary small undo" onClick={() => onMark(job, done, false)}>
          {MORE_PICTURES.undo}Undo
        </button>
      </article>
    );
  }

  // For everyone (owner, 2026-11-10): pressed without what a document needs, a toast says what.
  const existing = latest(job.documents);
  const documents = (
    <div className="job-docs">
      <MakeDocument doc={cv} kind="cv" existing={existing.cv} />
      <MakeDocument doc={cover} kind="cover" existing={existing.cover} />
    </div>
  );
  // Nobody applies here: the job opens where it was posted, and they apply there, then say so here
  // (owner, 2026-11-06). In that order along the card's foot, each its picture and its words, quieter than the
  // documents above them, and Skip, the way out, apart at the far end (owner, 2026-11-16: what every button
  // does is read off it "just by scrolling through").
  const marks = (
    <div className="job-bar">
      <a className="bar-action apply" href={job.url} target="_blank" rel="noreferrer">
        Apply on {job.apply_on}{PICTURES.apply}
      </a>
      <button type="button" className="bar-action" onClick={() => onMark(job, "applied", true)}>
        {PICTURES.applied}Mark as applied
      </button>
      <button type="button" className="bar-action skip" onClick={() => setSkipping(true)}>
        {PICTURES.skip}Skip
      </button>
    </div>
  );
  // Who, where and when, then what is worth a glance, each in a tag: how well it fits, whether it is new, the
  // pay.
  const head = (
    <>
      <Where job={job} />
      {job.fit != null || fresh || job.salary ? (
        <p className="job-tags">
          {job.fit != null ? (
            <span className="tag match" style={{ "--fit": job.fit } as CSSProperties}
                  title="How well this job fits your CV, cover letter and About you, from 1 to 10"
                  aria-label={`Match score: ${job.fit} out of 10`}>
              Match {job.fit}/10
            </span>
          ) : null}
          {fresh ? <span className="tag new">New</span> : null}
          {job.salary ? <span className="tag">{money(job.salary)}</span> : null}
        </p>
      ) : null}
    </>
  );
  // A job somebody added by its link is on the page for that, not for their words.
  const why = job.words.length > 0 && (
    <p className="why">
      Your words:{" "}
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
      <button type="button" className={saved ? "bookmark on" : "bookmark"} aria-pressed={saved} onClick={save}
              aria-label={saved ? "Remove bookmark" : "Bookmark this job"}>
        {PICTURES.bookmark}
      </button>
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
 * the download straight away for one made before and kept. On Jobs and on Applied alike. */
export function MakeDocument({ doc, kind, existing }: { doc: Build; kind: "cv" | "cover"; existing?: Made }) {
  const { build, making, start, error } = doc;
  const name = kind === "cv" ? "CV" : "cover letter";
  const ready = build?.state === "ready" && build.download_url ? build.download_url : null;
  // The CV is the one most people need, so the cover letter is the quieter of the two, made or not.
  if (ready || (!build && existing)) {
    return (
      <span className="make">
        <a className={kind === "cover" ? "button secondary" : "button"} href={ready ?? existing!.download_url} download>
          {MORE_PICTURES.download}Download {name}
        </a>
        {build?.saved === false ? (
          <span className="small muted">
            Not kept: your storage is full. <a href="#/documents">Make room</a>
          </span>
        ) : (
          <button type="button" className="again" onClick={() => void start()}>{MORE_PICTURES.again}Make it again</button>
        )}
      </span>
    );
  }
  return (
    <span className="make">
      <button type="button" disabled={making} onClick={() => void start()}
              className={[making && "making", kind === "cover" && "secondary"].filter(Boolean).join(" ")}>
        {making ? `Making your ${name}…` : `Make my ${name}`}
      </button>
      <Error>{error}</Error>
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
