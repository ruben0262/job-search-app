/**
 * What to search for.
 *
 * **The live match count is switched off as of 2026-07-31**, display and request both, commented
 * rather than deleted. The argument for it still stands and is why it is kept: a misspelled keyword
 * otherwise surfaces as an empty feed a day later with nothing to explain it, and the person
 * concludes the product does not work. `/onboarding/config/preview` is still served, so turning it
 * back on is two uncomments and restoring the `ConfigPreview` import.
 *
 * **Rebuilt for people who do not read forms** (owner, 2026-07-30). It was two textareas of
 * newline-separated text and a `<select multiple>` — three controls whose rules you have to be told:
 * that Enter makes a new item, that ctrl-click adds a second place, that a blank line is ignored.
 * Now search terms are chips you add one at a time, and places are labelled checkboxes in groups,
 * so the whole page can be operated without instructions.
 *
 * Locations stay a fixed list rather than free text, and that is not only a UI choice: the
 * harvester folds queries on a normalised location, so free text would spend the shared fan-out
 * budget on three spellings of one place.
 *
 * **How fresh a job must be is not here.** It lives in Settings beside the delivery interval, which
 * is the other half of the same question, and having it in two places meant nobody could say which
 * one was in force (owner, 2026-07-30).
 */
import { useEffect, useMemo, useState } from "react";

// `ConfigPreview` goes back on this import when the match count is switched on again.
import { api } from "../../api";
import { Error, useAction } from "../../ui";

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

/** Places, grouped the way somebody thinks about them rather than the order the enum happens to
 *  be in. Anything the schema adds that is not listed here still appears, under "Other". */
const PLACE_GROUPS: { label: string; hint: string; values: string[] }[] = [
  {
    label: "Work from anywhere",
    hint: "Remote roles, by where the company will hire from",
    values: ["remote_worldwide", "remote_eu", "remote_uk", "remote_us"],
  },
  {
    label: "Regions and countries",
    hint: "Anywhere in these, including roles that ask you on site",
    values: ["european_union", "europe", "united_kingdom", "ireland"],
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
  remote_worldwide: "Remote, anywhere in the world",
  remote_eu: "Remote, from the EU",
  remote_uk: "Remote, from the UK",
  remote_us: "Remote, from the US",
  european_union: "European Union",
  united_kingdom: "United Kingdom",
  europe: "Europe (including non-EU)",
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
  // **The live match count is switched off** (2026-07-31). The block that rendered it is commented
  // out below; the debounced request that fed it is commented out with it, because a POST every
  // 500 keystrokes whose answer nothing displays is pure cost on a page people edit slowly.
  // Both halves are kept rather than deleted — turning it back on is two uncomments and no
  // rewriting, and the reasoning for having it at all is in this file's header.
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
        // **Defaults under the stored section, not merely in place of a missing one.** A config
        // written before a field existed has the key absent rather than empty, and every list on
        // this form calls `.includes` on it — a stored section with no `locations` took the whole
        // page down until this spread was here.
        { ...BLANK.sections[0]!, ...(first ?? {}), keywords, exclude_keywords: excluded },
        ...rest,
      ],
    };
  }, [config, keywords, excluded]);

  // Debounced, because it runs while somebody is still typing and a request per keystroke is a
  // request per keystroke.
  //
  // useEffect(() => {
  //   if (!draft.sections[0]?.keywords.length) {
  //     setPreview(null);
  //     return;
  //   }
  //   const timer = window.setTimeout(() => {
  //     void api
  //       .post<ConfigPreview>("/onboarding/config/preview", draft)
  //       .then(setPreview)
  //       .catch(() => setPreview(null));
  //   }, 500);
  //   return () => window.clearTimeout(timer);
  // }, [draft]);

  const save = useAction(async () => {
    await api.put("/onboarding/config", draft);
    onDone();
  });

  const section = draft.sections[0]!;
  const patch = (changes: Partial<Section>) =>
    setConfig({ ...config, sections: [{ ...section, ...changes }, ...config.sections.slice(1)] });

  const toggleLocation = (value: string) =>
    patch({
      locations: section.locations.includes(value)
        ? section.locations.filter((each) => each !== value)
        : [...section.locations, value],
    });

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

      {/* {preview ? (
        <p className={preview.matches === 0 ? "error" : "muted"}>
          <strong>{preview.matches}</strong> matching jobs in the last {preview.window_days} days
          {preview.examples.length ? ` — e.g. ${preview.examples.slice(0, 2).join("; ")}` : ""}.
          <br />
          <span className="small">{preview.detail}</span>
        </p>
      ) : null} */}

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
          EU" is not the same as "in the EU" — it means the company hires people living there.
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
                    onChange={() => toggleLocation(value)}
                  />
                  <span>{title(value)}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}
        {section.locations.length === 0 ? (
          <p className="facts">Nothing picked yet — we will search everywhere.</p>
        ) : null}
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

/**
 * A list you build one item at a time. Enter or comma commits, backspace on an empty box removes
 * the last one — the pattern every tag input has taught, and the reason this is not a textarea:
 * a newline-separated box needs a sentence explaining that newlines separate.
 */
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
    // Case-insensitive: "Backend Engineer" and "backend engineer" are the same search, and two
    // chips that look identical is the kind of thing people assume is broken.
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
