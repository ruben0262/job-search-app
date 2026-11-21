/** Bookmarks, the second tab of Jobs: the jobs you put a bookmark on, each shown as the Jobs page shows
 * a job, the latest first. One leaves when you take its bookmark off, skip it or apply, or when it closes
 * (owner, 2026-11-15). */
import { JobsTabs, useJobList } from "./JobsPage";

/** The jobs they bookmarked, as `kept.ts` keeps them. */
export const BOOKMARKED = "/jobs/bookmarked";

export function BookmarksPage({ onChanged }: {
  /** "Mark as applied" changed the count the dashboard keeps. */
  onChanged: () => void;
}) {
  const { jobs, total: count, failed, card, more } = useJobList(BOOKMARKED, onChanged);
  return (
    <>
      <JobsTabs on="bookmarks" />
      <h1>{!jobs ? "Bookmarks" : count ? `${count} bookmarked job${count === 1 ? "" : "s"}` : "No bookmarks yet"}</h1>
      {jobs && !count ? (
        <p className="lede">Tap the bookmark in the corner of a job, and it is kept here for you.</p>
      ) : null}
      <div className="jobs">{(jobs ?? []).map(card)}</div>
      {more}
      {failed ? <p className="error">Your bookmarks did not load. Refresh the page to try again.</p> : null}
    </>
  );
}
