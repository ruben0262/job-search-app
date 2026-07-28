/**
 * Page 7 — what to search for, with a live match count.
 *
 * **The count is the point of this page.** A misspelled keyword otherwise surfaces as an empty feed
 * a day later with nothing to explain it, and the person concludes the product does not work. The
 * server says plainly that the number is approximate, and so does this form: the real search runs
 * against each site directly, so the true number is usually higher — but zero here almost always
 * means a typo.
 *
 * Locations are a picklist and not free text, and that is not only a UI choice: the harvester folds
 * queries on a normalised location, so free text would spend the shared fan-out budget on three
 * spellings of one place.
 */
import { useEffect, useMemo, useState } from "react";

import { api, type ConfigPreview } from "../../api";
import { Error, Field, useAction } from "../../ui";

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

const label = (value: string) => value.replaceAll("_", " ");

export function ConfigStep({ onDone }: { onDone: () => void }) {
  const [config, setConfig] = useState<Config>(BLANK);
  const [schema, setSchema] = useState<Schema | null>(null);
  const [preview, setPreview] = useState<ConfigPreview | null>(null);
  const [keywordText, setKeywordText] = useState("");
  const [excludeText, setExcludeText] = useState("");

  useEffect(() => {
    void api
      .get<{ config: Config | null; schema: Schema }>("/onboarding/config")
      .then((stored) => {
        setSchema(stored.schema);
        if (stored.config) {
          setConfig(stored.config);
          setKeywordText((stored.config.sections[0]?.keywords ?? []).join("\n"));
          setExcludeText((stored.config.sections[0]?.exclude_keywords ?? []).join("\n"));
        }
      })
      .catch(() => undefined);
  }, []);

  const options = (name: string): string[] => schema?.$defs?.[name]?.enum ?? [];

  const draft = useMemo<Config>(() => {
    const lines = (text: string) =>
      text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    const [first, ...rest] = config.sections;
    return {
      ...config,
      sections: [
        { ...(first ?? BLANK.sections[0]!), keywords: lines(keywordText), exclude_keywords: lines(excludeText) },
        ...rest,
      ],
    };
  }, [config, keywordText, excludeText]);

  // Debounced, because it runs while somebody is still typing and a request per keystroke is a
  // request per keystroke.
  useEffect(() => {
    if (!draft.sections[0]?.keywords.length) {
      setPreview(null);
      return;
    }
    const timer = window.setTimeout(() => {
      void api
        .post<ConfigPreview>("/onboarding/config/preview", draft)
        .then(setPreview)
        .catch(() => setPreview(null));
    }, 500);
    return () => window.clearTimeout(timer);
  }, [draft]);

  const save = useAction(async () => {
    await api.put("/onboarding/config", draft);
    onDone();
  });

  const section = draft.sections[0]!;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <Field label="What are you looking for?" hint="One search term per line. Up to 25.">
        <textarea
          value={keywordText}
          placeholder={"backend engineer\nfull-stack developer\nquant analyst"}
          style={{ minHeight: "7rem" }}
          onChange={(event) => setKeywordText(event.target.value)}
        />
      </Field>

      {preview ? (
        <p className={preview.matches === 0 ? "error" : "muted"}>
          <strong>{preview.matches}</strong> matching jobs in the last {preview.window_days} days
          {preview.examples.length ? ` — e.g. ${preview.examples.slice(0, 2).join("; ")}` : ""}.
          <br />
          <span className="small">{preview.detail}</span>
        </p>
      ) : null}

      <Field label="Anything to keep out?" hint="One per line. A job mentioning any of these is never shown.">
        <textarea
          value={excludeText}
          placeholder={"unpaid\ninternship"}
          style={{ minHeight: "4rem" }}
          onChange={(event) => setExcludeText(event.target.value)}
        />
      </Field>

      <Field label="Where?" hint="Pick as many as apply. Remote regions are not the same as the country itself.">
        <select
          multiple
          size={6}
          value={section.locations}
          onChange={(event) =>
            setConfig({
              ...config,
              sections: [
                { ...section, locations: [...event.target.selectedOptions].map((option) => option.value) },
                ...config.sections.slice(1),
              ],
            })
          }
        >
          {options("Location").map((value) => (
            <option key={value} value={value}>
              {label(value)}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Workplace">
        <select
          value={section.remote_policy}
          onChange={(event) =>
            setConfig({
              ...config,
              sections: [{ ...section, remote_policy: event.target.value }, ...config.sections.slice(1)],
            })
          }
        >
          {options("RemotePolicy").map((value) => (
            <option key={value} value={value}>
              {label(value)}
            </option>
          ))}
        </select>
      </Field>

      <Error>{save.error}</Error>
      <button type="submit" disabled={save.busy || !section.keywords.length}>
        {save.busy ? "Saving…" : "Save and continue"}
      </button>
    </form>
  );
}
