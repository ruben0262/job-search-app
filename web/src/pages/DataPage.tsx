/** Your details — everything onboarding collected, in one place, editable. */
import { useState, type ReactNode } from "react";

import { ConfigStep } from "./onboarding/ConfigStep";
import { ClaudeStep, ProxyStep, TelegramStep } from "./onboarding/Credentials";
import { DocumentsStep, HistoryStep } from "./onboarding/DocumentsStep";
import { IdentityStep } from "./onboarding/IdentityStep";

const SECTIONS: { key: string; title: string; hint: string }[] = [
  { key: "identity", title: "Name and links", hint: "What appears at the top of your CVs" },
  { key: "documents", title: "Documents", hint: "The files we read your profile from" },
  { key: "history", title: "Your history", hint: "The long-form write-up of what you have done" },
  { key: "config", title: "What we search for", hint: "Job titles, places, and how you want to work" },
  { key: "telegram", title: "Telegram", hint: "Where your jobs arrive" },
  { key: "claude", title: "Claude", hint: "The credential your documents run on" },
  { key: "proxy", title: "Proxy", hint: "What LinkedIn is fetched through" },
];

export function DataPage({ onChanged }: { onChanged: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const doneIn = (key: string) => () => {
    setSaved(key);
    onChanged();
    window.setTimeout(() => setSaved((current) => (current === key ? null : current)), 2500);
  };

  return (
    <>
      <p className="kicker">Your details</p>
      <div className="spread">
        <h1>Everything we have from you</h1>
      </div>
      <p className="muted" style={{ maxWidth: "44rem" }}>
        The answers you gave during setup, here to correct whenever something was wrong or has
        changed. Saving a section takes effect the same way it did the first time.
      </p>

      <div style={{ marginTop: "2rem", maxWidth: "44rem" }}>
        {SECTIONS.map((section) => (
          <Section
            key={section.key}
            title={section.title}
            hint={saved === section.key ? "Saved" : section.hint}
            open={open === section.key}
            onToggle={() => setOpen(open === section.key ? null : section.key)}
          >
            <SectionBody which={section.key} onDone={doneIn(section.key)} />
          </Section>
        ))}
      </div>
    </>
  );
}

function Section({
  title,
  hint,
  open,
  onToggle,
  children,
}: {
  title: string;
  hint: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`fold${open ? " open" : ""}`}>
      <button type="button" className="fold-head" onClick={onToggle} aria-expanded={open}>
        <span>
          <span className="fold-title">{title}</span>
          <span className={`fold-hint${hint === "Saved" ? " saved-flash" : ""}`}>{hint}</span>
        </span>
        <span className="fold-chevron" aria-hidden="true">
          ›
        </span>
      </button>
      {open ? <div className="fold-body page-in">{children}</div> : null}
    </section>
  );
}

function SectionBody({ which, onDone }: { which: string; onDone: () => void }) {
  switch (which) {
    case "identity":
      return <IdentityStep onDone={onDone} />;
    case "documents":
      return <DocumentsStep onDone={onDone} />;
    case "history":
      return <HistoryStep onDone={onDone} />;
    case "config":
      return <ConfigStep onDone={onDone} />;
    case "telegram":
      return <TelegramStep onDone={onDone} />;
    case "claude":
      return <ClaudeStep onDone={onDone} />;
    case "proxy":
      return <ProxyStep onDone={onDone} />;
    default:
      return null;
  }
}
