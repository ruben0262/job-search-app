/** Alerts (owner, 2026-11-17): named alerts, each following words, companies, places, job sites or a match score,
 * and sending what it finds to the bell, this phone, Telegram or the morning email. One main button: New alert.
 * `#/alerts?alert=<id>` is one alert's own list of what it found, where its bell line and its Telegram cards go. */
import { useState } from "react";

import { Alerts, AlertsForMembers, Pause } from "../alerts";
import { api, type Schema } from "../api";
import { useKept } from "../kept";
import { ago } from "../time";
import { toast } from "../toast";
import { Error, Field, Sheet, Words, useAction } from "../ui";
import { useJobList } from "./JobsPage";

type Page = Schema<"AlertsPage">;
type Alert = Schema<"AlertOut">;
type Saved = Schema<"AlertSaved">;
/** An alert as the form holds it: every field, the server's defaults filled in. */
type Draft = Required<Schema<"AlertIn">>;
type Channel = Draft["channels"][number];

/** What the page reads, as `kept.ts` keeps it. */
export const ALERTS = "/alerts";

const BLANK: Draft = {
  name: "", search: false, keywords: [], companies: [], places: [], sources: [], min_fit: null,
  channels: ["bell", "push", "telegram"], on: true,
};

/** Where an alert can send, said as a person would: on the form, a hint under it, and on the card. */
const CHANNELS: [Channel, string, string, string][] = [
  ["bell", "Here on the site", "Under the bell at the top", "the site"],
  ["push", "Your phone or browser", "Once notifications are on, below", "your phone"],
  ["telegram", "Telegram", "Once Telegram is connected, below", "Telegram"],
  ["email", "Morning email", "One email a day with what it found", "a morning email"],
];

export function AlertsPage({ onChanged, query }: { onChanged: () => void; query: string }) {
  const { data: page, failed, reload } = useKept<Page>(ALERTS);
  const [editing, setEditing] = useState<Alert | "new" | null>(null);
  const shown = Number(new URLSearchParams(query).get("alert")) || null;

  if (!page) return failed ? <p className="error">Your alerts did not load. Refresh the page.</p>
    : <p className="muted">Loading your alerts…</p>;
  const open = page.alerts.find((alert) => alert.id === shown);
  if (open) return <Found alert={open} days={page.found_days} onChanged={onChanged} />;

  const full = page.most !== null && page.alerts.length >= page.most;
  return (
    <>
      <h1>Alerts</h1>
      <p className="lede">
        Hear about a job the moment it appears: from a company you follow, in a city you want, or anything that
        matches your words.
      </p>
      {!page.can ? <AlertsForMembers /> : (
        <>
          <button type="button" className="main" disabled={full} onClick={() => setEditing("new")}>New alert</button>
          {full ? <p className="small muted">You have {page.most} alerts, the most you can have. Delete one to make another.</p>
            : null}
          {page.checked_at ? <p className="small muted">Checked for new jobs {ago(page.checked_at)}.</p> : null}
          <ul className="alert-list">
            {page.alerts.map((alert) => (
              <AlertCard key={alert.id} alert={alert} page={page} onEdit={() => setEditing(alert)} onChanged={reload} />
            ))}
          </ul>
          <section className="optional">
            <h2>Where alerts reach you</h2>
            <Alerts />
            <Pause onChanged={onChanged} />
          </section>
        </>
      )}
      {editing ? (
        <Sheet title={editing === "new" ? "New alert" : `Change “${editing.name}”`} onClose={() => setEditing(null)}>
          <AlertForm page={page} alert={editing === "new" ? null : editing}
                     onSaved={(saved) => {
                       setEditing(null);
                       toast({ text: wouldHaveFound(saved, page.found_days) });
                       void reload();
                       onChanged();
                     }} />
        </Sheet>
      ) : null}
    </>
  );
}

/** One alert: what it follows, where it sends, what it found lately, and a switch. */
function AlertCard({ alert, page, onEdit, onChanged }: {
  alert: Alert; page: Page; onEdit: () => void; onChanged: () => void;
}) {
  // Flipped the moment it is tapped, and back if the server says no: a switch that waits for an answer looks
  // dead.
  const [on, setOn] = useState(alert.on);
  const flip = useAction(async (next: boolean) => {
    setOn(next);
    try {
      await api.put(`/alerts/${alert.id}`, { ...draftOf(alert), on: next });
    } catch (error) {
      setOn(!next);
      throw error;
    }
    onChanged();
  });
  const remove = useAction(async () => {
    await api.del(`/alerts/${alert.id}`);
    onChanged();
  });
  const sends = CHANNELS.filter(([way]) => alert.channels.includes(way)).map(([, , , said]) => said);
  return (
    <li className={`card alert-card${on ? "" : " off"}`}>
      <div className="alert-head">
        <a className="alert-name" href={`#/alerts?alert=${alert.id}`}>{alert.name}</a>
        <label className="switch">
          <input type="checkbox" role="switch" checked={on} aria-label={`${alert.name} on`}
                 onChange={(event) => void flip.run(event.target.checked)} />
          {on ? "On" : "Off"}
        </label>
      </div>
      <p className="small">{follows(alert, page)}</p>
      <p className="small muted">
        {alert.found ? `${alert.found} found in the last ${page.found_days} days` : "Nothing found yet"}
        {sends.length ? ` · Sends to ${sends.join(", ")}` : " · Sends nowhere"}
      </p>
      <div className="row">
        <a className="button secondary small" href={`#/alerts?alert=${alert.id}`}>See what it found</a>
        <button type="button" className="secondary small" onClick={onEdit}>Change</button>
        <button type="button" className="linklike small" disabled={remove.busy}
                onClick={() => void remove.run()}>Delete</button>
      </div>
      <Error>{flip.error ?? remove.error}</Error>
    </li>
  );
}

/** What an alert follows, in one line: "Your Jobs list · LEGO · Denmark, Berlin · score 7+". */
function follows(alert: Alert, page: Page): string {
  const label = (options: Page["places"], values: string[]) =>
    values.map((value) => options.find((option) => option.value === value)?.label ?? value).join(", ");
  return [
    alert.search ? "Your Jobs list" : "",
    alert.keywords.join(", "),
    alert.companies.join(", "),
    label(page.places, alert.places),
    label(page.sources, alert.sources),
    alert.min_fit ? `match score ${alert.min_fit}+` : "",
  ].filter(Boolean).join(" · ");
}

/** What an alert would have found lately, said as it is saved: one that finds nothing is seen to then, not after
 * days of silence. */
function wouldHaveFound(saved: Saved, days: number): string {
  const count = saved.would_have_found;
  return count ? `“${saved.name}” would have found ${count} job${count === 1 ? "" : "s"} in the last ${days} days.`
    : `“${saved.name}” would have found nothing in the last ${days} days. Fewer words, companies or places find `
      + "more.";
}

function draftOf(alert: Alert): Draft {
  const { name, search, keywords, companies, places, sources, min_fit, channels, on } = alert;
  return { name, search, keywords, companies, places, sources, min_fit, channels, on };
}

/** Making or changing an alert: a name, what to look for, where, and where to send it. */
function AlertForm({ page, alert, onSaved }: {
  page: Page; alert: Alert | null; onSaved: (saved: Saved) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => (alert ? draftOf(alert) : BLANK));
  const set = (change: Partial<Draft>) => setDraft((now) => ({ ...now, ...change }));
  const save = useAction(async () => {
    onSaved(await (alert ? api.put<Saved>(`/alerts/${alert.id}`, draft) : api.post<Saved>(ALERTS, draft)));
  });
  const toggle = <T,>(values: T[], value: T) =>
    values.includes(value) ? values.filter((each) => each !== value) : [...values, value];

  return (
    <form className="alert-form" onSubmit={(event) => { event.preventDefault(); void save.run(); }}>
      <Field label="Name" hint="So you know which alert sent a job.">
        <input value={draft.name} maxLength={60} placeholder="LEGO in Denmark" required
               onChange={(event) => set({ name: event.target.value })} />
      </Field>
      <label className="pick">
        <input type="checkbox" checked={draft.search} onChange={(event) => set({ search: event.target.checked })} />
        <span>Only jobs on my Jobs list<span className="pick-hint">Your words from My search must match too.</span></span>
      </label>
      <Words tone="green" label="Words" hint="Any of these in the job's title or text." values={draft.keywords}
             onChange={(keywords) => set({ keywords })} />
      <Words tone="plain" label="Companies" hint="Jobs from any of these employers." placeholder="Type a company"
             values={draft.companies} onChange={(companies) => set({ companies })} />
      <Choose label="Places" hint="Countries or cities. None: anywhere." options={page.places}
              values={draft.places} onChange={(places) => set({ places })} />
      <fieldset className="picker">
        <legend>Job sites</legend>
        <p className="picker-hint">None picked: every site.</p>
        <div className="toggles">
          {page.sources.map((source) => (
            <button key={source.value} type="button" className="toggle"
                    aria-pressed={draft.sources.includes(source.value)}
                    onClick={() => set({ sources: toggle(draft.sources, source.value) })}>
              {source.label}
            </button>
          ))}
        </div>
      </fieldset>
      <Field label="Match score" hint={page.scores ? "How well a job fits your CV, 1 to 10."
        : "Upload your CV in My documents to use this."}>
        <select value={draft.min_fit ?? ""} disabled={!page.scores && !draft.min_fit}
                onChange={(event) => set({ min_fit: event.target.value ? Number(event.target.value) : null })}>
          <option value="">Any score</option>
          {[5, 6, 7, 8, 9].map((score) => <option key={score} value={score}>{score} or more</option>)}
        </select>
      </Field>
      <fieldset className="picker">
        <legend>Send new jobs to</legend>
        {CHANNELS.map(([way, label, hint]) => (
          <label key={way} className="pick">
            <input type="checkbox" checked={draft.channels.includes(way)}
                   onChange={() => set({ channels: toggle(draft.channels, way) })} />
            <span>{label}<span className="pick-hint">{hint}</span></span>
          </label>
        ))}
      </fieldset>
      <Error>{save.error}</Error>
      <button type="submit" className="main" disabled={save.busy || !draft.name.trim()}>
        {save.busy ? "Saving…" : alert ? "Save" : "Make this alert"}
      </button>
    </form>
  );
}

/** Picking from a long list by typing: each picked one a chip, the list offered as the box is typed in. */
function Choose({ label, hint, options, values, onChange }: {
  label: string; hint: string; options: Page["places"]; values: string[]; onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const named = (value: string) => options.find((option) => option.value === value)?.label ?? value;
  const add = (typed: string) => {
    const found = options.find((option) => option.label.toLowerCase() === typed.trim().toLowerCase());
    if (found && !values.includes(found.value)) onChange([...values, found.value]);
    if (found) setDraft("");
  };
  const list = `${label.toLowerCase()}-options`;
  return (
    <div className="field">
      <span className="label">{label}</span>
      <span className="hint">{hint}</span>
      <div className="chips">
        {values.map((value) => (
          <span key={value} className="word plain">
            {named(value)}
            <button type="button" aria-label={`Remove ${named(value)}`}
                    onClick={() => onChange(values.filter((each) => each !== value))}>×</button>
          </span>
        ))}
      </div>
      <div className="row add-word">
        <input list={list} value={draft} aria-label={`Add to: ${label}`} placeholder="Type a place"
               onChange={(event) => {
                 setDraft(event.target.value);
                 // Picked from the browser's list: added at once, no Add to press.
                 if (options.some((option) => option.label === event.target.value)) add(event.target.value);
               }}
               onKeyDown={(event) => {
                 if (event.key === "Enter") {
                   event.preventDefault();
                   add(draft);
                 }
               }} />
        <button type="button" className="add plain" onClick={() => add(draft)}>+ Add</button>
        <datalist id={list}>
          {options.filter((option) => !values.includes(option.value)).map((option) => (
            <option key={option.value} value={option.label} />
          ))}
        </datalist>
      </div>
    </div>
  );
}

/** One alert's own list: what it found, the latest first, each job as the Jobs page shows it. */
function Found({ alert, days, onChanged }: { alert: Alert; days: number; onChanged: () => void }) {
  const { jobs, failed, card } = useJobList(`/alerts/${alert.id}/jobs`, onChanged);
  return (
    <>
      <p><a className="small" href="#/alerts">← All alerts</a></p>
      <h1>{alert.name}</h1>
      <p className="lede">
        {!jobs ? "Loading what it found…" : jobs.length ? `${alert.found} found in the last ${days} days, the newest first.`
          : "Nothing yet. The moment a job matches, it is here."}
      </p>
      <div className="jobs">{(jobs ?? []).map(card)}</div>
      {failed ? <p className="error">What it found did not load. Refresh the page to try again.</p> : null}
    </>
  );
}
