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

export function Banner({ level, children }: { level: "error" | "warning" | "info"; children: ReactNode }) {
  return <p className={`banner ${level}`}>{children}</p>;
}
