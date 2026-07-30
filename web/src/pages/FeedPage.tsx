/**
 * The jobs, as a real table: the columns that matter, filters on them, search, pagination.
 *
 * **The server does the filtering.** Every control here becomes a query parameter and the page
 * refetches — the alternative (filter the loaded page in the client) silently lies whenever the
 * answer lives on a page that was never fetched.
 *
 * **It says which stage ranked them.** Before a trained model exists this is keyword matching, and
 * presenting that as "your model" would make the product look broken the moment the real model
 * arrives and reorders everything.
 */
import { useEffect, useRef, useState } from "react";

import { api, type Feed, type FeedJob, type Salary } from "../api";

const PAGE = 25;

type Sort = "score" | "posted" | "title" | "company";
type AppliedFilter = "any" | "applied" | "not";

/** "<1h" / "3h" / "2d" — the resolution somebody scanning a feed actually reads at. */
function age(iso: string | null): string | null {
  if (!iso) return null;
  const hours = Math.floor((Date.now() - new Date(iso).getTime()) / 3_600_000);
  if (hours < 0) return null;
  if (hours < 1) return "<1h";
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** "€60–80k / yr". Compact, because a salary column that wraps is a column nobody can scan. */
function money(salary: Salary): string {
  if (!salary || salary.min == null) return "—";
  const symbol = { EUR: "€", USD: "$", GBP: "£" }[salary.currency] ?? `${salary.currency} `;
  const compact = (value: number) =>
    value >= 1_000_000
      ? `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`
      : value >= 10_000
        ? `${Math.round(value / 1000)}k`
        : `${value}`;
  const range =
    salary.max != null && salary.max !== salary.min
      ? `${compact(salary.min)}–${compact(salary.max)}`
      : compact(salary.min);
  const period = { year: "yr", month: "mo", day: "day", hour: "hr" }[salary.period];
  return `${symbol}${range}${period ? ` / ${period}` : ""}`;
}

export function FeedPage() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [failed, setFailed] = useState(false);

  const [query, setQuery] = useState("");
  const [source, setSource] = useState("");
  const [maxAgeDays, setMaxAgeDays] = useState("");
  const [remote, setRemote] = useState(false);
  const [applied, setApplied] = useState<AppliedFilter>("any");
  const [sort, setSort] = useState<Sort>("score");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);

  // Typing shouldn't fire a request per keystroke, and a late response for an old query must not
  // overwrite the answer to the current one — the sequence number guards that.
  const requestSeq = useRef(0);
  useEffect(() => {
    const seq = ++requestSeq.current;
    const params = new URLSearchParams({ limit: `${PAGE}`, offset: `${offset}`, sort, direction });
    if (query.trim()) params.set("q", query.trim());
    if (source) params.set("source", source);
    if (maxAgeDays) params.set("max_age_days", maxAgeDays);
    if (remote) params.set("remote", "true");
    if (applied !== "any") params.set("applied", applied === "applied" ? "true" : "false");

    const timer = window.setTimeout(() => {
      api
        .get<Feed>(`/feed?${params}`)
        .then((body) => {
          if (requestSeq.current === seq) setFeed(body);
        })
        .catch(() => {
          if (requestSeq.current === seq) setFailed(true);
        });
    }, query ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [query, source, maxAgeDays, remote, applied, sort, direction, offset]);

  // A changed filter starts the answer over; staying on page 3 of a different question is nonsense.
  const filter = <T,>(set: (value: T) => void) => (value: T) => {
    set(value);
    setOffset(0);
  };

  const heading = (key: Sort, label: string) => (
    <th
      className={`sortable${sort === key ? " sorted" : ""}`}
      onClick={() => {
        if (sort === key) setDirection(direction === "desc" ? "asc" : "desc");
        else {
          setSort(key);
          setDirection(key === "title" || key === "company" ? "asc" : "desc");
        }
        setOffset(0);
      }}
    >
      {label}
      {sort === key ? (direction === "desc" ? " ↓" : " ↑") : ""}
    </th>
  );

  if (failed) return <p className="error">The feed did not load. Refresh to try again.</p>;
  if (!feed) return <p className="muted">Loading…</p>;

  const from = feed.total === 0 ? 0 : offset + 1;
  const to = offset + feed.jobs.length;

  return (
    <>
      <p className="kicker">The feed</p>
      <div className="spread">
        <h1>Jobs</h1>
        <span className="small muted">
          {feed.stage === "personal" ? "Ranked by your model" : "Ranked by your keywords"}
        </span>
      </div>
      {feed.detail ? <p className="muted small">{feed.detail}</p> : null}

      <div className="toolbar">
        <input
          type="search"
          placeholder="Search titles, companies, links"
          value={query}
          onChange={(event) => filter(setQuery)(event.target.value)}
        />
        <select value={source} onChange={(event) => filter(setSource)(event.target.value)}>
          <option value="">All sources</option>
          {feed.sources.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <select value={maxAgeDays} onChange={(event) => filter(setMaxAgeDays)(event.target.value)}>
          <option value="">Any age</option>
          <option value="1">Last 24h</option>
          <option value="3">Last 3 days</option>
          <option value="7">Last week</option>
          <option value="30">Last month</option>
        </select>
        <button type="button" className={`chip${remote ? " on" : ""}`} onClick={() => filter(setRemote)(!remote)}>
          Remote
        </button>
        <button
          type="button"
          className={`chip${applied !== "any" ? " on" : ""}`}
          onClick={() =>
            filter(setApplied)(applied === "any" ? "applied" : applied === "applied" ? "not" : "any")
          }
        >
          {applied === "any" ? "Applied: all" : applied === "applied" ? "Applied" : "Not applied"}
        </button>
      </div>

      {feed.jobs.length === 0 ? (
        <p className="muted">
          {feed.total === 0 && !query && !source && !maxAgeDays && !remote && applied === "any"
            ? "Nothing yet. If this stays empty, your search terms may be narrower than you meant — check them in settings."
            : "Nothing matches these filters."}
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              {heading("score", "Score")}
              {heading("title", "Title")}
              {heading("company", "Company")}
              <th>Location</th>
              <th>Source</th>
              {heading("posted", "Age")}
              <th>Salary</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {feed.jobs.map((job) => (
              <Row key={job.id} job={job} />
            ))}
          </tbody>
        </table>
      )}

      <div className="pager">
        <span>
          {from}–{to} of {feed.total}
        </span>
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          ← Prev
        </button>
        <button disabled={to >= feed.total} onClick={() => setOffset(offset + PAGE)}>
          Next →
        </button>
      </div>
    </>
  );
}

function Row({ job }: { job: FeedJob }) {
  const posted = age(job.posted_at);
  const seen = age(job.first_seen_at);
  return (
    <tr>
      <td className="num">{job.score === null ? "—" : <span className="score">{Math.round(job.score)}</span>}</td>
      <td>
        <a className="job-title" href={job.url} target="_blank" rel="noreferrer">
          {job.title}
        </a>
        {job.is_remote ? <span className="job-sub"> · remote</span> : null}
      </td>
      <td className="job-sub">{job.company ?? "—"}</td>
      <td className="job-sub">{job.location ?? "—"}</td>
      <td className="job-sub">{job.source}</td>
      {/* Posted age when the source reported one; first-seen with a ~ when it did not, because
          "how long has this been out" deserves an honest approximation over a blank. */}
      <td className="num job-sub">{posted ?? (seen ? (seen === "<1h" ? "new" : `~${seen}`) : "—")}</td>
      <td className="num job-sub">{money(job.salary)}</td>
      <td>{job.applied_at ? <span className="tag">applied</span> : null}</td>
    </tr>
  );
}
