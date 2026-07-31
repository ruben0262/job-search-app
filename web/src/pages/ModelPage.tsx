/**
 * Your model — where the scorer is trained, first time and every time after.
 *
 * **Split out of the profile chat on purpose** (owner, 2026-07-30): the chat edits what the
 * platform knows about you; this page is about the model that ranks jobs with it. The two were on
 * one screen and even the owner could not say where training happened — which meant nobody else
 * ever would.
 *
 * The page states the loop plainly — label, train, rank — because "why is my feed still keyword
 * matching" has a three-step answer, and a user who can see which step they are on does not write
 * a support message.
 */
import { useState } from "react";

import { api, type Dashboard } from "../api";
import { Error, useAction } from "../ui";

export function ModelPage({
  dashboard,
  onChanged,
}: {
  dashboard: Dashboard;
  onChanged: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);

  const start = useAction(async () => {
    const body = await api.post<{ message: string }>("/setup/start");
    setMessage(body.message);
    onChanged();
  });

  if (dashboard.stage === "onboarding") return null;
  const { model, feed, next } = dashboard;

  // The columns come from the newest run: it measured the most, and an older row simply leaves the
  // cells it has no answer for empty rather than the table losing a column for everyone.
  const columns = model.history?.[0]?.metrics ?? [];

  const headline = model.servable
    ? `Your model is live — version ${model.version}`
    : feed.labels > 0
      ? "Your model is being built"
      : "Your model does not exist yet";

  return (
    <div className="overview">
      <p className="kicker">Your model</p>
      <h1>{headline}</h1>
      <p className="lede">
        {model.servable
          ? "Every job is ranked by a model trained on your own decisions. It keeps learning: every CV you build and every rating you give becomes training data."
          : "Until it is trained, your feed is ranked by your keywords. The model needs labelled jobs to learn from — that is what scoring is."}
      </p>

      <dl className="stats">
        <div className="stat">
          <dt>{feed.labels.toLocaleString()}</dt>
          <dd>labelled jobs to learn from</dd>
        </div>
        <div className="stat">
          <dt>{feed.scored.toLocaleString()}</dt>
          <dd>jobs scored for you</dd>
        </div>
        <div className="stat">
          <dt>{model.servable ? `v${model.version}` : "—"}</dt>
          <dd>
            {model.servable && model.trained_at
              ? `trained ${new Date(model.trained_at).toLocaleDateString()}`
              : "no model yet"}
          </dd>
        </div>
      </dl>

      {!model.servable && model.stale_reason ? (
        <p className="facts">{model.stale_reason}</p>
      ) : null}

      {/* The one action. Labelling is the expensive step (it runs on your Claude key), so it is a
          button rather than a side effect — and retraining afterwards is free and automatic. */}
      <div style={{ marginTop: "1.5rem" }}>
        {next.action === "confirm" || next.action === "process" || next.action === "upload" ? (
          <p className="facts">
            Scoring needs a finished profile first —{" "}
            <a href={next.action === "confirm" ? "#/process" : "#/profile"}>
              {next.action === "confirm" ? "review your profile" : "build your profile"}
            </a>{" "}
            and come back.
          </p>
        ) : (
          <>
            <Error>{start.error}</Error>
            {message ? <p className="facts" style={{ color: "var(--score)" }}>{message}</p> : null}
            <button disabled={start.busy} onClick={() => void start.run()}>
              {start.busy
                ? "Starting…"
                : feed.labels > 0
                  ? "Score more jobs"
                  : "Score my first jobs"}
            </button>
            <p className="facts" style={{ marginTop: "0.75rem" }}>
              Runs on your own Claude key and takes a few minutes. Retraining afterwards is free
              and happens by itself as labels accumulate.
            </p>
          </>
        )}
      </div>

      {/* **Every version, not just the live one.** "Is this getting better" is a question about the
          sequence, and it is the question somebody paying for their own training has. All of this
          was already measured and stored at fit time; until now the only way to read it was SQL. */}
      {model.history?.[0] ? (
        <div style={{ marginTop: "3rem" }}>
          <h2>Version history</h2>
          <p className="facts">
            What each training run measured. <strong>Cutoff</strong> is the score a job must beat to
            reach you — tuned on held-out predictions rather than picked, so it moves between
            versions.
          </p>
          <div className="table-scroll">
            <table className="history">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Trained</th>
                  <th>Labels</th>
                  {columns.map((metric) => (
                    <th key={metric.key}>{metric.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {model.history.map((entry) => (
                  <tr key={entry.version} className={entry.active ? "on" : undefined}>
                    <td>
                      v{entry.version}
                      {entry.active ? <span className="pill">live</span> : null}
                    </td>
                    <td>
                      {entry.trained_at
                        ? new Date(entry.trained_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td>{entry.n_labels?.toLocaleString() ?? "—"}</td>
                    {columns.map((column) => {
                      const cell = entry.metrics.find((metric) => metric.key === column.key);
                      // Absent rather than zero: the earliest runs recorded nothing but a label
                      // count, and a 0.0 would read as a model that scored nothing rather than one
                      // nobody measured.
                      return <td key={column.key}>{cell ? String(cell.value) : "—"}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div style={{ marginTop: "3rem" }}>
        <h2>How it learns</h2>
        <ol className="loop">
          <li>
            <strong>Label.</strong> Claude reads jobs against your profile and scores them — these
            are the examples the model learns from.
          </li>
          <li>
            <strong>Train.</strong> A model is fitted to those examples on our hardware. Free, and
            automatic once there are enough.
          </li>
          <li>
            <strong>Rank.</strong> Every new job is scored by your model the moment it is
            collected. Building a CV counts as a strong yes; skipping counts as a no — the model
            retrains on your behaviour.
          </li>
        </ol>
        <p className="facts">
          Changing your profile makes old labels stale — the platform tells you when that happens,
          and relabelling is offered rather than run behind your back, because it costs real money.
        </p>
      </div>
    </div>
  );
}
