/** What to search for. */
import { useEffect, useMemo, useState } from "react";

import { api } from "../../api";
import type { components } from "../../api-types";
import { Error, useAction } from "../../ui";

/** Generated from the server, so a choice list it stops sending fails the build, not the page. */
type ConfigOut = components["schemas"]["ConfigOut"];
type Choices = components["schemas"]["ConfigChoices"];

/** Mirrors `MAX_LOCATIONS_PER_USER` in libs/contracts/config.py. */
const MAX_LOCATIONS = 12;

type Section = {
  platform: string;
  keywords: string[];
  exclude_keywords: string[];
  locations: string[];
  remote_policy: string;
  posted_within_hours: number;
};

type Config = {
  sections: Section[];
  seniority: string[];
  languages: string[];
  min_salary: number | null;
  salary_currency: string;
};

const BLANK: Config = {
  sections: [
    {
      platform: "all",
      keywords: [],
      exclude_keywords: [],
      locations: [],
      remote_policy: "remote_or_hybrid",
      posted_within_hours: 24,
    },
  ],
  seniority: [],
  languages: [],
  min_salary: null,
  salary_currency: "EUR",
};

/** Places, grouped the way somebody thinks about them rather than the order the enum happens to be in. */
const PLACE_GROUPS: { label: string; hint: string; values: string[] }[] = [
  {
    label: "Regions and countries",
    hint: "Where you can take a job. How you work — remote, hybrid or on site — is the next question",
    values: ["europe", "united_kingdom", "ireland", "switzerland", "norway"],
  },
  {
    label: "Cities",
    hint: "Roles in and around a specific city",
    values: ["london", "berlin", "amsterdam", "dublin", "paris", "madrid", "lisbon", "warsaw",
             "budapest", "zurich", "stockholm", "copenhagen"],
  },
];

export function ConfigStep({ onDone }: { onDone: () => void }) {
  const [config, setConfig] = useState<Config>(BLANK);
  const [choices, setChoices] = useState<Choices | null>(null);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);

  useEffect(() => {
    void api
      .get<ConfigOut>("/onboarding/config")
      .then((stored) => {
        setChoices(stored.choices);
        // Theirs, or filled in from their CV until they save their own.
        const saved = stored.config as Config | null;
        if (saved) {
          setConfig(saved);
          setKeywords(saved.sections[0]?.keywords ?? []);
          setExcluded(saved.sections[0]?.exclude_keywords ?? []);
        }
      })
      .catch(() => undefined);
  }, []);

  const labels = new Map((choices?.locations ?? []).map((choice) => [choice.value, choice.label]));
  const title = (value: string) => labels.get(value) ?? value;
  const known = [...labels.keys()];
  const grouped = PLACE_GROUPS.map((group) => ({
    ...group,
    values: group.values.filter((value) => known.includes(value)),
  }));
  const listed = new Set(grouped.flatMap((group) => group.values));
  const other = known.filter((value) => !listed.has(value));

  const draft = useMemo<Config>(() => {
    const [first, ...rest] = config.sections;
    return {
      ...config,
      sections: [
        // Defaults under the stored section, not merely in place of a missing one.
        { ...BLANK.sections[0]!, ...(first ?? {}), keywords, exclude_keywords: excluded },
        ...rest,
      ],
    };
  }, [config, keywords, excluded]);

  const save = useAction(async () => {
    await api.put("/onboarding/config", draft);
    onDone();
  });

  const section = draft.sections[0]!;
  const patch = (changes: Partial<Section>) =>
    setConfig({ ...config, sections: [{ ...section, ...changes }, ...config.sections.slice(1)] });

  // The schema's bound, enforced where the choices are made.
  const atLimit = section.locations.length >= MAX_LOCATIONS;

  const toggleLanguage = (code: string) =>
    setConfig((current) => ({
      ...current,
      languages: current.languages.includes(code)
        ? current.languages.filter((each) => each !== code)
        : [...current.languages, code],
    }));

  const toggleLocation = (value: string) => {
    const chosen = section.locations.includes(value);
    if (!chosen && atLimit) return;
    patch({
      locations: chosen
        ? section.locations.filter((each) => each !== value)
        : [...section.locations, value],
    });
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <ChipInput
        label="What job titles are you after?"
        hint="Add them one at a time. We search for each separately, so two or three specific titles beat one vague one."
        placeholder="e.g. backend engineer"
        values={keywords}
        onChange={setKeywords}
        max={25}
      />

      <ChipInput
        label="Anything you never want to see?"
        hint="A job whose title or company mentions one of these is never sent. Leave it empty if nothing comes to mind."
        placeholder="e.g. unpaid"
        values={excluded}
        onChange={setExcluded}
        max={25}
      />

      <div className="field">
        <span className="label">Where would you work?</span>
        <span className="hint">
          Tick everything that applies. Picking a country includes its cities, and "remote from the
          EU" is not the same as "in the EU", it means the company hires people living there.
        </span>
        {grouped.map((group) =>
          group.values.length ? (
            <fieldset className="picker" key={group.label}>
              <legend>{group.label}</legend>
              <p className="picker-hint">{group.hint}</p>
              <div className="picker-grid">
                {group.values.map((value) => (
                  <label key={value} className="pick">
                    <input
                      type="checkbox"
                      checked={section.locations.includes(value)}
                      disabled={atLimit && !section.locations.includes(value)}
                      onChange={() => toggleLocation(value)}
                    />
                    <span>{title(value)}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          ) : null,
        )}
        {other.length ? (
          <fieldset className="picker">
            <legend>Other</legend>
            <div className="picker-grid">
              {other.map((value) => (
                <label key={value} className="pick">
                  <input
                    type="checkbox"
                    checked={section.locations.includes(value)}
                    disabled={atLimit && !section.locations.includes(value)}
                    onChange={() => toggleLocation(value)}
                  />
                  <span>{title(value)}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}
        {section.locations.length === 0 ? (
          <p className="facts">Nothing picked yet, we will search everywhere.</p>
        ) : (
          <p className={atLimit ? "pick-count full" : "pick-count"}>
            {section.locations.length} of {MAX_LOCATIONS} places picked
            {atLimit ? ". Untick one to choose somewhere else." : ""}
          </p>
        )}
      </div>

      <div className="field">
        <span className="label">Which languages can you work in?</span>
        <span className="hint">
          A posting written in a language you have not picked is not shown to you, and the
          documents we write for you follow the same list. Pick nothing and we show you everything.
        </span>
        <div className="picker-grid" style={{ marginTop: "0.5rem" }}>
          {(choices?.languages ?? []).map((language) => (
            <label key={language.value} className="pick">
              <input
                type="checkbox"
                checked={config.languages.includes(language.value)}
                onChange={() => toggleLanguage(language.value)}
              />
              <span>{language.label}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="field">
        <span className="label">How often would you go in?</span>
        <div className="picker-grid" style={{ marginTop: "0.5rem" }}>
          {(choices?.remote_policies ?? []).map((option) => (
            <label key={option.value} className="pick">
              <input
                type="radio"
                name="remote_policy"
                checked={section.remote_policy === option.value}
                onChange={() => patch({ remote_policy: option.value })}
              />
              <span>
                {option.label}
                <span className="pick-hint">{option.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      <Error>{save.error}</Error>
      <button type="submit"
              className={section.keywords.length ? "advance ready" : "advance"}
              disabled={save.busy}>
        {save.busy ? "Saving…" : "Save and continue"}
      </button>
    </form>
  );
}

/** A list you build one item at a time. */
function ChipInput({
  label,
  hint,
  placeholder,
  values,
  onChange,
  max,
}: {
  label: string;
  hint: string;
  placeholder: string;
  values: string[];
  onChange: (values: string[]) => void;
  max: number;
}) {
  const [draft, setDraft] = useState("");

  const commit = () => {
    const value = draft.trim().replace(/,$/, "").trim();
    // Case-insensitive: "Backend Engineer" and "backend engineer" are the same search.
    if (!value || values.length >= max) return setDraft("");
    if (values.some((each) => each.toLowerCase() === value.toLowerCase())) return setDraft("");
    onChange([...values, value]);
    setDraft("");
  };

  return (
    <div className="field">
      <span className="label">{label}</span>
      <span className="hint">{hint}</span>
      <div className="chips">
        {values.map((value) => (
          <span className="chip-tag" key={value}>
            {value}
            <button
              type="button"
              aria-label={`Remove ${value}`}
              onClick={() => onChange(values.filter((each) => each !== value))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="row" style={{ gap: "0.5rem" }}>
        <input
          value={draft}
          placeholder={values.length >= max ? `That is the ${max} we can search` : placeholder}
          disabled={values.length >= max}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === ",") {
              event.preventDefault();
              commit();
            } else if (event.key === "Backspace" && !draft && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          onBlur={commit}
        />
        <button type="button" className="secondary" disabled={!draft.trim()} onClick={commit}>
          Add
        </button>
      </div>
    </div>
  );
}
