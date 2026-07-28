/**
 * The jobs.
 *
 * **It says which stage ranked them.** Before a trained model exists this is keyword matching, and
 * presenting that as "your model" would make the product look broken the moment the real model
 * arrives and reorders everything.
 */
import { useEffect, useState } from "react";

import { api, type Feed } from "../api";
import { Card } from "../ui";

export function FeedPage() {
  const [feed, setFeed] = useState<Feed | null>(null);

  useEffect(() => {
    void api.get<Feed>("/feed").then(setFeed).catch(() => undefined);
  }, []);

  if (!feed) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="spread">
        <h1>Jobs</h1>
        <span className="small muted">
          {feed.stage === "personal" ? "Ranked by your model" : "Ranked by your keywords"}
        </span>
      </div>

      {feed.detail ? <p className="muted small">{feed.detail}</p> : null}

      <Card>
        {feed.jobs.length === 0 ? (
          <p className="muted">
            Nothing yet. If this stays empty, your search terms may be narrower than you meant —
            check them in settings.
          </p>
        ) : (
          feed.jobs.map((job) => (
            <div className="job" key={job.id}>
              <div className="spread">
                <a href={job.url} target="_blank" rel="noreferrer">
                  {job.title}
                </a>
                {job.score === null ? null : <span className="score">{Math.round(job.score)}</span>}
              </div>
              <div className="small muted">
                {[job.company, job.location, job.is_remote ? "remote" : null, job.source]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
          ))
        )}
      </Card>
    </>
  );
}
