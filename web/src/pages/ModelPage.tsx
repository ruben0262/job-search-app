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
import { useCallback, useEffect, useState } from "react";

import { api, type Dashboard } from "../api";
import { Error, useAction } from "../ui";


type Training = {
  running: boolean;
  phase: string;
  labels: number;
  target: number;
  stale_labels: number;
  lanes: Record<string, { ready: number; claimed: number; done: number; failed: number }>;
  failures: Record<string, string>;
  paused?: boolean;
};

/** What each phase is, in the user's terms. The lane names are ours. */
const PHASE_TEXT: Record<string, string> = {
  cold_start: "Reading job postings and deciding which ones suit you",
  relabel: "Re-judging jobs you already had, against your updated profile",
  train: "Fitting your model to what it learned",
  confirm_profile: "Waiting on your profile",
};

export function ModelPage({
  dashboard,
  onChanged,
}: {
  dashboard: Dashboard;
  onChanged: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);

  const [training, setTraining] = useState<Training | null>(null);

  /**
   * Poll while anything is running.
   *
   * **The work never lived in the browser.** Pressing the button writes rows to Postgres and the
   * worker pools drain them, so closing the tab or switching pages changes nothing about whether it
   * finishes. What was missing was any way to see that: the page said "Starting…" once and then
   * looked identical whether the run was halfway through or had failed an hour ago.
   */
  const readTraining = useCallback(async () => {
    const body = await api.get<Training>("/process/training").catch(() => null);
    if (body) setTraining(body);
  }, []);

  useEffect(() => {
    void readTraining();
  }, [readTraining]);

  // **Poll while there is work left, not only while a row happens to be queued.** Keying on
  // `running` alone meant that once the page had seen an idle moment between batches it stopped
  // asking and never noticed the run continuing -- and a page opened fresh mid-run never started
  // polling at all, because its first answer arrived during one of those gaps.
  // A stopped run has work left in it by definition, so the phase alone would keep the page
  // polling for ever. Paused ends it.
  const unfinished =
    training?.paused !== true &&
    (training?.running === true ||
      ["cold_start", "relabel", "train"].includes(training?.phase ?? ""));

  useEffect(() => {
    if (!unfinished) return;
    const timer = window.setInterval(() => void readTraining(), 3000);
    return () => window.clearInterval(timer);
  }, [unfinished, readTraining]);

  const stop = useAction(async () => {
    await api.post("/process/training/stop");
    // **Reflected before the next poll.** The server has set the flag by the time this returns, so
    // waiting three seconds to show it left the user pressing Stop and watching "Working…" — which
    // reads as the button having done nothing, and is what made them press it again.
    setTraining((current) => (current ? { ...current, running: false, paused: true } : current));
    await readTraining();
  });

  const start = useAction(async () => {
    const body = await api.post<{ message: string }>("/setup/start");
    setMessage(body.message);
    await readTraining();
    onChanged();
  });

  if (dashboard.stage === "onboarding") return null;
  const { model, feed } = dashboard;

  // The columns come from the newest run: it measured the most, and an older row simply leaves the
  // cells it has no answer for empty rather than the table losing a column for everyone.
  const columns = model.history?.[0]?.metrics ?? [];

  const headline = model.servable
    ? `Your model is live, version ${model.version}`
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
          : "Until it is trained, your feed is ranked by your keywords. The model needs labelled jobs to learn from, that is what scoring is."}
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
          button rather than a side effect, and retraining afterwards is free and automatic. */}
      <div style={{ marginTop: "1.5rem" }}>
        {/* **Gated on setup, not on the old capture flow's state.** This used to read `next.action`,
            which belongs to the superseded document-processing step and knows nothing about the
            profile files, so an account with all eight written was told to go and build a profile
            it had already finished. `may_train` is the same check the trainer itself refuses on. */}
        {!dashboard.may_train ? (
          <p className="facts">
            Scoring needs a finished profile first.{" "}
            <a href="#/profile">Build your profile</a> and come back.
          </p>
        ) : (
          <>
            <Error>{start.error}</Error>
            <Error>{stop.error}</Error>
            {message && !training?.running ? (
              <p className="facts" style={{ color: "var(--score)" }}>{message}</p>
            ) : null}

            {training?.running ? (
              /* **Running, and saying so.** The three dots are the same signal the setup chat
                 uses, because it means the same thing there: something is happening that this
                 page did not have to be open for. */
              <div className="working" style={{ marginBottom: "0.9rem" }}>
                <span className="typing" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                <span className="muted small">
                  {PHASE_TEXT[training.phase] ?? "Working"}
                  {training.phase === "cold_start" || training.phase === "relabel"
                    ? ` — ${training.labels.toLocaleString()} of ${training.target.toLocaleString()} jobs judged`
                    : ""}
                </span>
              </div>
            ) : null}

            {training?.running &&
            (training.phase === "cold_start" || training.phase === "relabel") ? (
              <div className="progress-rail" aria-hidden="true">
                <span
                  style={{
                    width: `${Math.min(100, Math.round((training.labels / Math.max(training.target, 1)) * 100))}%`,
                  }}
                />
              </div>
            ) : null}

            {training?.running ? (
              <button
                className="secondary"
                disabled={stop.busy}
                onClick={() => {
                  /* Asked, because stopping is not free: the labels already bought are kept, but
                     the run has to be started again from where it reached. */
                  if (
                    window.confirm(
                      "Stop training? Nothing new will be picked up. A batch already running will "
                        + "finish, and every job judged so far is kept — you can start again from here.",
                    )
                  )
                    void stop.run();
                }}
              >
                {stop.busy ? "Stopping…" : "Stop training"}
              </button>
            ) : (
              <button disabled={start.busy} onClick={() => void start.run()}>
                {start.busy
                  ? "Starting…"
                  : feed.labels > 0
                    ? "Score more jobs"
                    : "Score my first jobs"}
              </button>
            )}

            {training && Object.keys(training.failures).length ? (
              <p className="error" style={{ marginTop: "0.75rem" }}>
                Something failed and has stopped: {Object.values(training.failures)[0]}
              </p>
            ) : null}

            <p className="facts" style={{ marginTop: "0.75rem" }}>
              Runs on your own Claude key. <strong>It keeps going if you close this page</strong> —
              the work runs on the server, not in your browser. Retraining afterwards is free and
              happens by itself as labels accumulate.
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
            reach you, tuned on held-out predictions rather than picked, so it moves between
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
            <strong>Label.</strong> Claude reads jobs against your profile and scores them, these
            are the examples the model learns from.
          </li>
          <li>
            <strong>Train.</strong> A model is fitted to those examples on our hardware. Free, and
            automatic once there are enough.
          </li>
          <li>
            <strong>Rank.</strong> Every new job is scored by your model the moment it is
            collected. Building a CV counts as a strong yes; skipping counts as a no, the model
            retrains on your behaviour.
          </li>
        </ol>
        <p className="facts">
          Changing your profile makes old labels stale, the platform tells you when that happens,
          and relabelling is offered rather than run behind your back, because it costs real money.
        </p>
      </div>
    </div>
  );
}
