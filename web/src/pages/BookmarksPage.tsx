/** Bookmarks, the second tab of Jobs: the jobs you put a bookmark on, each shown as the Jobs page shows
 * a job, the latest first. One leaves when you take its bookmark off, skip it or apply, or when it closes
 * (owner, 2026-11-15). Searched and filtered as Jobs is (owner, 2026-11-24: "a basic search bar and filtering
 * function too"), for as long as the site is open. */
import { useEffect, useState } from "react";

import { FilterSheet, Finder, FiltersOn, type Narrow, SitesSection, WhereSection, WIDE, narrowed, narrowing,
         showing } from "../finder";
import { keep, peek } from "../kept";
import { JobsTabs, useJobList } from "./JobsPage";

/** The jobs they bookmarked, as `kept.ts` keeps them. */
export const BOOKMARKED = "/jobs/bookmarked";
/** The filters as this tab last had them (`kept.ts`): coming back to Bookmarks finds them as they were. */
const FILTERS = `${BOOKMARKED}/filters`;

export function BookmarksPage({ onChanged }: {
  /** "Mark as applied" changed the count the dashboard keeps. */
  onChanged: () => void;
}) {
  const [narrow, setNarrow] = useState<Narrow>(() => peek<Narrow>(FILTERS) ?? { q: "", ...WIDE });
  const set = (change: Partial<Narrow>) => setNarrow((now) => ({ ...now, ...change }));
  useEffect(() => keep(FILTERS, narrow), [narrow]);
  // Typing does not ask once a key: the search the list is asked for follows a moment after the last one.
  const [query, setQuery] = useState(() => `${narrowed(narrow)}`);
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(`${narrowed(narrow)}`), narrow.q ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [narrow]);
  const { jobs, total: count, offers, failed, card, more } = useJobList(BOOKMARKED, onChanged, query);
  const [choosing, setChoosing] = useState(false);
  const on = offers ? narrowing(narrow, offers) : [];
  const filtered = narrow.q.trim() !== "" || on.length > 0;
  const clear = () => setNarrow({ q: "", ...WIDE });
  const bookmarks = (n: number) => `${n} ${n === 1 ? "bookmark" : "bookmarks"}`;
  return (
    <>
      <JobsTabs on="bookmarks" />
      <h1>
        {!jobs ? "Bookmarks"
          : filtered ? `${bookmarks(count)} ${count === 1 ? "matches" : "match"} your search`
            : count ? `${count} bookmarked job${count === 1 ? "" : "s"}` : "No bookmarks yet"}
      </h1>
      {jobs && !count && !filtered ? (
        <p className="lede">Tap the bookmark in the corner of a job, and it is kept here for you.</p>
      ) : null}
      {count || filtered ? (
        <>
          <Finder query={narrow.q} onQuery={(q) => set({ q })} label="Search bookmarks" on={on.length}
                  onOpen={() => setChoosing(true)} />
          <FiltersOn on={on} set={set} clear={clear} />
        </>
      ) : null}
      {choosing && offers ? (
        <FilterSheet on={on.length > 0} clear={clear} showing={showing(!jobs, count)}
                     onClose={() => setChoosing(false)}>
          <WhereSection narrow={narrow} set={set} offers={offers} />
          <SitesSection narrow={narrow} set={set} offers={offers} />
        </FilterSheet>
      ) : null}
      {jobs && !count && filtered ? (
        <p className="muted">
          None of your bookmarks match.{" "}
          <button type="button" className="secondary small" onClick={clear}>Clear filters</button>
        </p>
      ) : null}
      <div className="jobs">{(jobs ?? []).map(card)}</div>
      {more}
      {failed ? <p className="error">Your bookmarks did not load. Refresh the page to try again.</p> : null}
    </>
  );
}
