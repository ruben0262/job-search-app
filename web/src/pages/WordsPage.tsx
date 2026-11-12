/** My search: the words that pick and hide your jobs, where you can work, your languages. One Save.
 *
 * The same form is onboarding's second screen ("What we found"), with your name and links on top.
 */
import { useEffect, useState } from "react";

import { api, type Config, type Schema } from "../api";
import { useKept } from "../kept";
import { askBeforeLeaving, go } from "../leave";
import { Error, Field, Prompt, Words, useAction } from "../ui";

type Review = Schema<"ReviewPage">;
type Section = Config["sections"][number];

/** What the words are for: My search's title, and in onboarding the heading of their part of the form. */
const WANTED = {
  title: "The jobs you want",
  // What the words do, said so they are chosen with care (owner, 2026-11-12).
  lede: "Green words are what we search for: a job shows up only if it has one. Red words hide jobs. Choose them carefully.",
};

export function WordsPage() {
  const [saved, setSaved] = useState(false);
  return (
    <>
      <h1>{WANTED.title}</h1>
      <p className="lede">{WANTED.lede}</p>
      <WordsForm action="Save" askBeforeLeaving onSaved={() => setSaved(true)} onEdited={() => setSaved(false)} />
      {saved ? <p className="saved-flash" role="status">Saved. Your jobs are updated.</p> : null}
    </>
  );
}

/** Your name and links, as they go on top of every CV we make. */
export function IdentityFields({ name, links, onName, onLinks }: {
  name: string;
  links: string[];
  onName: (name: string) => void;
  onLinks: (links: string[]) => void;
}) {
  return (
    <>
      <Field label="Your name">
        <input value={name} autoComplete="name" onChange={(event) => onName(event.target.value)} />
      </Field>
      <Words tone="plain" label="Your links" chipClass="link-chip"
             hint="LinkedIn, GitHub or a portfolio. Optional."
             placeholder="linkedin.com/in/you" values={links} onChange={onLinks}
             display={(url) => url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")} />
    </>
  );
}

/** What the form reads, as `kept.ts` keeps it. */
export const REVIEW = "/onboarding/review";

export function WordsForm({ withIdentity = false, askBeforeLeaving: guarded = false, action, onSaved, onEdited }: {
  withIdentity?: boolean;
  /** Ask, with a choice to save, before anybody leaves the page with changes not saved. */
  askBeforeLeaving?: boolean;
  action: string;
  onSaved: () => void;
  onEdited?: () => void;
}) {
  // What was read last time at once (`kept.ts`), then what is read now, unless they have started editing.
  const { data: review, reload } = useKept<Review>(REVIEW);
  const [config, setConfig] = useState<Config | null>(() => (review?.config as Config | undefined) ?? null);
  const [name, setName] = useState(() => review?.identity.full_name ?? "");
  const [links, setLinks] = useState<string[]>(() => review?.identity.links.map((link) => link.url) ?? []);
  // Changed since it was read or saved; and where somebody tried to go meanwhile, while it asks.
  const [dirty, setDirty] = useState(false);
  const [leaving, setLeaving] = useState<string | null>(null);
  useEffect(() => {
    askBeforeLeaving(guarded && dirty ? setLeaving : null);
    return () => askBeforeLeaving(null);
  }, [guarded, dirty]);

  useEffect(() => {
    if (!review || dirty) return;
    // Theirs, or filled in from their CV until they save their own.
    setConfig(review.config as Config);
    setName(review.identity.full_name ?? "");
    setLinks(review.identity.links.map((link) => link.url));
    // Only a new answer resets the form, never an edit.
  }, [review]);

  const store = async () => {
    if (withIdentity) {
      await api.put("/onboarding/identity", { full_name: name, links: links.map((url) => ({ url })) });
    }
    await api.put("/onboarding/config", config);
    setDirty(false);
    onSaved();
    // What is kept for next time is what was saved.
    void reload();
  };
  const save = useAction(store);
  const saveAndGo = useAction(async (to: string) => {
    await store();
    go(to);
  });

  if (!review || !config) return <p className="muted">Reading what we found…</p>;

  const { choices, suggestions } = review;
  const section = config.sections[0]!;
  const edit = (next: Config) => {
    setConfig(next);
    setDirty(true);
    onEdited?.();
  };
  const patch = (changes: Partial<Section>) =>
    edit({ ...config, sections: [{ ...section, ...changes }] });
  const toggle = <T,>(list: T[], value: T) =>
    list.includes(value) ? list.filter((each) => each !== value) : [...list, value];
  const places = section.locations ?? [];
  const full = places.length >= choices.max_locations;
  const languages = config.languages ?? [];
  const ways = section.workplaces ?? [];
  // What the server refuses to save, said before Save: the least a search takes, and a name for the CV.
  const short = choices.min_words - section.keywords.length;
  const missing = [
    short > 0 ? `add ${short} more green word${short === 1 ? "" : "s"}` : null,
    places.length ? null : "pick a country",
    ways.length ? null : "pick how you work",
    languages.length ? null : "pick a language",
    withIdentity && !name.trim() ? "type your name" : null,
  ].filter(Boolean);
  const ready = missing.length === 0;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) void save.run();
      }}
    >
      {withIdentity ? (
        <section className="form-section">
          <h2>About you</h2>
          <p className="section-lede">On top of every CV we make.</p>
          <IdentityFields name={name} links={links} onName={setName} onLinks={setLinks} />
        </section>
      ) : null}

      <section className="form-section">
        {/* Onboarding's screen is "What we found", so the part says what it is; My search's title says it. */}
        {withIdentity ? (
          <>
            <h2>{WANTED.title}</h2>
            <p className="section-lede">{WANTED.lede}</p>
          </>
        ) : null}
        <Words tone="green" label="Show me jobs with these words" warnLong
               hint={`At least ${choices.min_words}, more is better. Like “nurse” or “python”.`}
               placeholder="Add a job title or skill"
               values={section.keywords} suggested={suggestions.green}
               suggestedTitle="Suggested from your CV — tap one to add it"
               onChange={(keywords) => patch({ keywords })} />
        <Words tone="red" label="Hide jobs with these words" warnLong
               hint="Like “senior” or “sales”. Optional." placeholder="Add a word to hide"
               values={section.exclude_keywords ?? []} suggested={suggestions.red}
               suggestedTitle="Suggested — tap one to hide those jobs"
               onChange={(exclude_keywords) => patch({ exclude_keywords })} />
      </section>

      <section className="form-section">
        <h2>Where and how you work</h2>
        <p className="section-lede">At least one of each.</p>

        <Countries options={choices.locations} picked={places} full={full}
                   onChange={(locations) => patch({ locations })} />
        {full ? <p className="picker-hint">That is the most countries you can pick: {choices.max_locations}.</p> : null}

        <fieldset className="picker">
          <legend>Which languages can you work in?</legend>
          <div className="picker-grid">
            {choices.languages.map((language) => {
              const value = language.value as NonNullable<Config["languages"]>[number];
              return (
                <label key={language.value} className="pick">
                  <input type="checkbox" role="switch" checked={languages.includes(value)}
                         disabled={last(languages, value)}
                         onChange={() => edit({ ...config, languages: toggle(languages, value) })} />
                  <span>{language.label}</span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <fieldset className="picker">
          <legend>How do you want to work?</legend>
          <div className="picker-grid">
            {choices.workplaces.map((way) => {
              const value = way.value as NonNullable<Section["workplaces"]>[number];
              return (
                <label key={way.value} className="pick">
                  <input type="checkbox" role="switch" checked={ways.includes(value)}
                         disabled={last(ways, value)}
                         onChange={() => patch({ workplaces: toggle(ways, value) })} />
                  <span>
                    {way.label}
                    <span className="pick-hint">{way.hint}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      </section>

      <Error>{save.error}</Error>
      {!ready ? <p className="small muted">To go on: {missing.join(", ")}.</p> : null}
      <button type="submit" className="main" disabled={!ready || save.busy}>
        {save.busy ? "Saving…" : action}
      </button>
      {leaving !== null ? (
        <Prompt title="Save your changes?" onClose={() => setLeaving(null)}>
          <p className="small muted">You changed your search and did not save it. If you leave now, the changes are lost.</p>
          <Error>{saveAndGo.error}</Error>
          <div className="row">
            <button type="button" disabled={!ready || saveAndGo.busy} onClick={() => void saveAndGo.run(leaving)}>
              {saveAndGo.busy ? "Saving…" : "Save and go"}
            </button>
            <button type="button" className="secondary" onClick={() => go(leaving)}>Leave without saving</button>
            <button type="button" className="linklike" onClick={() => setLeaving(null)}>Stay here</button>
          </div>
        </Prompt>
      ) : null}
    </form>
  );
}

/** The only one switched on, which stays on: a search always has a country, a way of working and a
 * language (the server refuses one without). */
const last = <T,>(chosen: T[], value: T) => chosen.length === 1 && chosen[0] === value;

type Choice = Review["choices"]["locations"][number];
type Place = NonNullable<Section["locations"]>[number];

/** How many countries show before the rest are opened: the three people pick most. */
const SHOWN = 3;

/** Every country a button, pressed when picked (owner, 2026-11-02). The first few show, with every one
 * picked; the rest are folded under one button until somebody wants another. */
function Countries({ options, picked, full, onChange }: {
  options: Choice[];
  picked: Place[];
  full: boolean;
  onChange: (picked: Place[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const on = (option: Choice) => picked.includes(option.value as Place);
  const shown = open ? options : options.filter((option, index) => index < SHOWN || on(option));
  return (
    <fieldset className="picker">
      <legend>Which countries?</legend>
      <div className="toggles">
        {shown.map((option) => (
          <button key={option.value} type="button" className="toggle" aria-pressed={on(option)}
                  disabled={(full && !on(option)) || last(picked, option.value as Place)}
                  onClick={() => onChange(on(option) ? picked.filter((each) => each !== option.value)
                    : [...picked, option.value as Place])}>
            {option.label}
          </button>
        ))}
      </div>
      <button type="button" className="linklike more" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Show fewer countries" : `Show ${options.length - shown.length} more countries`}
      </button>
    </fieldset>
  );
}
