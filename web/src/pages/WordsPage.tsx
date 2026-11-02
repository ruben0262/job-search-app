/** My search: the words that pick and hide your jobs, where you can work, your languages. One Save.
 *
 * The same form is onboarding's second screen ("What we found"), with your name and links on top.
 */
import { useEffect, useState } from "react";

import { api, type Config, type Schema } from "../api";
import { askBeforeLeaving, go } from "../leave";
import { Error, Field, Prompt, Words, useAction } from "../ui";

type Review = Schema<"ReviewPage">;
type Section = Config["sections"][number];

export function WordsPage() {
  const [saved, setSaved] = useState(false);
  return (
    <>
      <p className="kicker">My search</p>
      <h1>What we look for</h1>
      <p className="lede">Green words show you jobs. Red words hide them.</p>
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
      <Field label="Your name" hint="Exactly as it should appear at the top of your CV.">
        <input value={name} autoComplete="name" onChange={(event) => onName(event.target.value)} />
      </Field>
      <Words tone="plain" label="Your links" chipClass="link-chip"
             hint="Shown under your name on every CV we make, so an employer can look you up. Add your LinkedIn, GitHub or a portfolio, or leave this empty."
             placeholder="linkedin.com/in/you" values={links} onChange={onLinks}
             display={(url) => url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")} />
    </>
  );
}

export function WordsForm({ withIdentity = false, askBeforeLeaving: guarded = false, action, onSaved, onEdited }: {
  withIdentity?: boolean;
  /** Ask, with a choice to save, before anybody leaves the page with changes not saved. */
  askBeforeLeaving?: boolean;
  action: string;
  onSaved: () => void;
  onEdited?: () => void;
}) {
  const [review, setReview] = useState<Review | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [name, setName] = useState("");
  const [links, setLinks] = useState<string[]>([]);
  // Changed since it was read or saved; and where somebody tried to go meanwhile, while it asks.
  const [dirty, setDirty] = useState(false);
  const [leaving, setLeaving] = useState<string | null>(null);
  useEffect(() => {
    askBeforeLeaving(guarded && dirty ? setLeaving : null);
    return () => askBeforeLeaving(null);
  }, [guarded, dirty]);

  useEffect(() => {
    void api.get<Review>("/onboarding/review").then((body) => {
      setReview(body);
      // Theirs, or filled in from their CV until they save their own.
      setConfig(body.config as Config);
      setName(body.identity.full_name ?? "");
      setLinks(body.identity.links.map((link) => link.url));
    });
  }, []);

  const store = async () => {
    if (withIdentity) {
      await api.put("/onboarding/identity", { full_name: name, links: links.map((url) => ({ url })) });
    }
    await api.put("/onboarding/config", config);
    setDirty(false);
    onSaved();
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
    edit({ ...config, sections: [{ ...section, ...changes }, ...config.sections.slice(1)] });
  const toggle = <T,>(list: T[], value: T) =>
    list.includes(value) ? list.filter((each) => each !== value) : [...list, value];
  const places = section.locations ?? [];
  const full = places.length >= choices.max_locations;
  // Without one green word no job can match, and without a name there is nothing to put on a CV.
  const ready = section.keywords.length > 0 && (!withIdentity || name.trim() !== "");

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
          <p className="section-lede">This goes at the top of every CV and cover letter we make for you.</p>
          <IdentityFields name={name} links={links} onName={setName} onLinks={setLinks} />
        </section>
      ) : null}

      <section className="form-section">
        <h2>The jobs you want</h2>
        <p className="section-lede">
          Green words find jobs: a job shows up when one of them is in it. Red words hide a job.
        </p>
        <Words tone="green" label="Show me jobs with these words" warnLong
               hint="One or two words work best, like “python” or “nurse”: a job title you would apply for, or a skill. A job needs at least one to be shown."
               values={section.keywords} suggested={suggestions.green}
               suggestedTitle="Suggested from your CV — tap one to add it"
               onChange={(keywords) => patch({ keywords })} />
        <Words tone="red" label="Hide jobs with these words" warnLong
               hint="One or two words work best. A job with one of these in its title or company is never shown."
               values={section.exclude_keywords ?? []} suggested={suggestions.red}
               suggestedTitle="Suggested — tap one to hide those jobs"
               onChange={(exclude_keywords) => patch({ exclude_keywords })} />
      </section>

      <section className="form-section">
        <h2>Where and how you work</h2>
        <p className="section-lede">Leave a list empty and it does not narrow anything.</p>

        <Countries options={choices.locations} picked={places} full={full}
                   onChange={(locations) => patch({ locations })} />
        {full ? <p className="picker-hint">That is the most countries you can pick: {choices.max_locations}.</p> : null}

        <fieldset className="picker">
          <legend>Which languages can you work in?</legend>
          <p className="picker-hint">A job written in another language is not shown. Pick nothing to see all.</p>
          <div className="picker-grid">
            {choices.languages.map((language) => {
              const value = language.value as NonNullable<Config["languages"]>[number];
              const chosen = config.languages ?? [];
              return (
                <label key={language.value} className="pick">
                  <input type="checkbox" role="switch" checked={chosen.includes(value)}
                         onChange={() => edit({ ...config, languages: toggle(chosen, value) })} />
                  <span>{language.label}</span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <fieldset className="picker">
          <legend>How often would you go in?</legend>
          <div className="picker-grid">
            {choices.remote_policies.map((policy) => (
              <label key={policy.value} className="pick">
                <input type="radio" name="remote_policy" checked={section.remote_policy === policy.value}
                       onChange={() => patch({ remote_policy: policy.value as Section["remote_policy"] })} />
                <span>
                  {policy.label}
                  <span className="pick-hint">{policy.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      </section>

      <Error>{save.error}</Error>
      {!ready ? (
        <p className="small muted">
          {section.keywords.length ? "Type your name to go on." : "Add at least one green word to go on."}
        </p>
      ) : null}
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
      <p className="picker-hint">Tap a country to see its jobs. Pick none, and no country is left out.</p>
      <div className="toggles">
        {shown.map((option) => (
          <button key={option.value} type="button" className="toggle" aria-pressed={on(option)}
                  disabled={full && !on(option)}
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
