/** The handful of pieces every page needs. */
import type { ReactNode } from "react";
import { useState } from "react";

import { ApiError } from "./api";

/** Runs an async action, keeping its pending and error state. */
export function useAction<A extends unknown[]>(fn: (...args: A) => Promise<unknown>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (...args: A) => {
    setBusy(true);
    setError(null);
    try {
      await fn(...args);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return { run, busy, error };
}

export function Error({ children }: { children: ReactNode }) {
  return children ? <p className="error">{children}</p> : null;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="label">{label}</span>
      {hint ? <span className="hint">{hint}</span> : null}
      {children}
    </label>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title ? <h2>{title}</h2> : null}
      {children}
    </section>
  );
}

/** A `div`, so it can hold the button that fixes what it says. */
export function Banner({ level, children }: { level: "error" | "warning" | "info"; children: ReactNode }) {
  return <div className={`banner ${level}`}>{children}</div>;
}

/** Green shows jobs, red hides them, plain is anything else built one item at a time (links). */
type Tone = "green" | "red" | "plain";

/** One suggested word: tap it to take it. The reason is said, so nobody has to guess why. */
export function Suggested({ tone, term, reason, onTake, onDismiss }: {
  tone: Tone;
  term: string;
  reason: string;
  onTake: () => void;
  onDismiss?: () => void;
}) {
  return (
    <li className="suggestion">
      <button type="button" className={`word ${tone} offer`} onClick={onTake}>
        {tone === "green" ? "+" : "−"} {term}
      </button>
      <span className="reason">{reason}</span>
      {onDismiss ? (
        <button type="button" className="dismiss" aria-label={`No thanks: ${term}`} onClick={onDismiss}>
          ×
        </button>
      ) : null}
    </li>
  );
}

/** Green words show jobs, red words hide them: the chips, the words worth adding, and a box. */
export function Words({ tone, label, hint, values, onChange, suggested = [], suggestedTitle,
                        placeholder = "Type a word", chipClass = "", display = (value) => value,
                        warnLong = false }: {
  tone: Tone;
  label: string;
  hint: string;
  values: string[];
  onChange: (values: string[]) => void;
  suggested?: { term: string; reason: string }[];
  /** What the suggestions are, said above them; they are offers, not words already chosen. */
  suggestedTitle?: string;
  placeholder?: string;
  chipClass?: string;
  /** How a value is shown, when that differs from what is stored (a link without its https://). */
  display?: (value: string) => string;
  /** Say so when a word is a long phrase: a board searches it as one thing and finds little. */
  warnLong?: boolean;
}) {
  const [draft, setDraft] = useState("");
  // Case-insensitive: "Nurse" and "nurse" are the same word to the matcher.
  const has = (word: string) => values.some((each) => each.toLowerCase() === word.toLowerCase());
  const add = (word: string) => {
    const clean = word.trim().replace(/,$/, "").trim();
    if (clean && !has(clean)) onChange([...values, clean]);
    setDraft("");
  };

  return (
    <div className="field">
      <span className="label">{label}</span>
      <span className="hint">{hint}</span>
      <div className="chips">
        {values.map((value) => (
          <span key={value} className={`word ${tone} ${chipClass}`.trim()} title={value}>
            {display(value)}
            <button type="button" aria-label={`Remove ${value}`}
                    onClick={() => onChange(values.filter((each) => each !== value))}>
              ×
            </button>
          </span>
        ))}
      </div>
      {warnLong && values.some((value) => value.trim().split(/\s+/).length > 2) ? (
        <p className="small long-words" role="note">
          Long phrases find fewer jobs:{" "}
          {values.filter((value) => value.trim().split(/\s+/).length > 2).map((value) => `“${value}”`).join(", ")}.
          One or two words work best.
        </p>
      ) : null}
      {suggested.some((each) => !has(each.term)) ? (
        <div className="suggested-box">
          <p className="suggested-title">
            {suggestedTitle ?? (tone === "red" ? "Suggested — tap one to hide those jobs" : "Suggested — tap one to add it")}
          </p>
          <ul className="suggestions">
            {suggested.filter((each) => !has(each.term)).map((each) => (
              <Suggested key={each.term} tone={tone} term={each.term} reason={each.reason}
                         onTake={() => add(each.term)} />
            ))}
          </ul>
        </div>
      ) : null}
      <div className="row add-word">
        <input
          value={draft}
          aria-label={`Add to: ${label}`}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === ",") {
              event.preventDefault();
              add(draft);
            }
          }}
        />
        <button type="button" className="secondary" disabled={!draft.trim()} onClick={() => add(draft)}>
          Add
        </button>
      </div>
    </div>
  );
}

/** One big button that takes a file, tapped or dropped on. Quiet when it is not the page's main thing. */
export function UploadButton({ label, busy, accept, quiet = false, onFile }: {
  label: string;
  busy: boolean;
  /** The file types the server reads, as it lists them. */
  accept: string[];
  quiet?: boolean;
  onFile: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={["drop", over && "over", quiet && "quiet"].filter(Boolean).join(" ")}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const file = event.dataTransfer.files[0];
        if (file && !busy) onFile(file);
      }}
    >
      <input
        type="file"
        accept={accept.join(",")}
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
          event.target.value = "";
        }}
      />
      <span className="drop-button">{busy ? "Reading your file…" : label}</span>
      <span className="drop-hint">or drop it here · PDF, Word or a text file</span>
    </label>
  );
}
