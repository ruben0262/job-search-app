/** What to search for. */
import { useEffect, useMemo, useState } from "react";

// `ConfigPreview` goes back on this import when the match count is switched on again.
import { api } from "../../api";
import { Error, useAction } from "../../ui";

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
  max_notifications_per_day: number;
};

/** Read from the schema the server exports, so a new location cannot exist on one side only. */
type Schema = { $defs?: Record<string, { enum?: string[] }> };

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
  max_notifications_per_day: 20,
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

/** Plain names for the values that do not read well spelled out. */
const PLACE_NAMES: Record<string, string> = {
  united_kingdom: "United Kingdom",
  // Named for what it admits.
  europe: "European Union (27 countries)",
};

const WORKPLACE: { value: string; label: string; hint: string }[] = [
  { value: "remote_only", label: "Remote only", hint: "Never asks you to come in" },
  { value: "remote_or_hybrid", label: "Remote or hybrid", hint: "Some days at home is fine" },
  { value: "any", label: "Anything", hint: "Including full-time on site" },
];

const title = (value: string) =>
  PLACE_NAMES[value] ?? value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

export function ConfigStep({ onDone }: { onDone: () => void }) {
  const [config, setConfig] = useState<Config>(BLANK);
  const [schema, setSchema] = useState<Schema | null>(null);
  // The live match count is switched off (2026-07-31).
  const [keywords, setKeywords] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);

  useEffect(() => {
    void api
      .get<{ config: Config | null; schema: Schema }>("/onboarding/config")
      .then((stored) => {
        setSchema(stored.schema);
        if (stored.config) {
          setConfig(stored.config);
          setKeywords(stored.config.sections[0]?.keywords ?? []);
          setExcluded(stored.config.sections[0]?.exclude_keywords ?? []);
        }
      })
      .catch(() => undefined);
  }, []);

  const known = (schema?.$defs?.Location?.enum ?? []) as string[];
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

  // Debounced: it runs while somebody is still typing, and a request per keystroke is waste.

  const save = useAction(async () => {
    await api.put("/onboarding/config", draft);
    onDone();
  });

  const section = draft.sections[0]!;
  const patch = (changes: Partial<Section>) =>
    setConfig({ ...config, sections: [{ ...section, ...changes }, ...config.sections.slice(1)] });

  // The schema's bound, enforced where the choices are made.
  const atLimit = section.locations.length >= MAX_LOCATIONS;

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

      {/* {preview ? */}

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
        <span className="label">How often would you go in?</span>
        <div className="picker-grid" style={{ marginTop: "0.5rem" }}>
          {WORKPLACE.map((option) => (
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
