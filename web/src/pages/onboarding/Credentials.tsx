/** Telegram, Claude and the proxy. Telegram is one link rather than a credential.
 *
 * Every credential block reads in the same order: the box you type into, then what is attached,
 * then the fold explaining how to get it. Someone arriving with the value in their clipboard never
 * has to scroll past instructions they do not need, and someone stuck finds the instructions where
 * the field ran out of answers.
 */
import { useCallback, useEffect, useState } from "react";

import { ClaudeConnect } from "../../components/ClaudeConnect";
import { ApiError, api, type Account } from "../../api";
import type { components } from "../../api-types";
import { Error, Field, useAction } from "../../ui";

type StoredCredential = { kind: string; last_4: string | null; validated: boolean };

/** What each deletion actually costs, named. */
const CONFIRM: Record<string, string> = {
  claude_api:
    "Remove your Claude API key? The profile chat and CV building stop working until you add "
    + "another one.",
  claude_oauth:
    "Remove your Claude token? The profile chat and CV building stop working until you add "
    + "another one.",
  proxy:
    "Remove your proxy? LinkedIn jobs stop being collected for you, every other source keeps "
    + "working.",
  default: "Remove this credential? Whatever depends on it stops working until you add another.",
};

/** What is stored for one credential kind: a masked preview, a copy that costs the account password. */
function Stored({
  kind,
  onChanged,
  version = 0,
}: {
  kind: string;
  onChanged: () => void;
  /** Bumped by the caller whenever a credential is written. */
  version?: number;
}) {
  const [stored, setStored] = useState<StoredCredential | null>(null);
  const [asking, setAsking] = useState(false);
  const [password, setPassword] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    api
      .get<{ credentials: StoredCredential[] }>("/credentials")
      .then((body) => setStored(body.credentials.find((row) => row.kind === kind) ?? null))
      .catch(() => undefined);
  }, [kind]);

  useEffect(() => {
    load();
  }, [load, version]);

  const copy = useAction(async () => {
    const body = await api.post<{ value: string }>(`/credentials/${kind}/reveal`, { password });
    await navigator.clipboard.writeText(body.value);
    setPassword("");
    setAsking(false);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2500);
  });

  const remove = useAction(async () => {
    await api.del(`/credentials/${kind}`);
    load();
    onChanged();
  });

  if (!stored) return null;

  return (
    <div className="stored">
      <span className="stored-value">
        ••••••••{stored.last_4 ?? ""}
        {!stored.validated ? <span className="stored-note"> · unchecked</span> : null}
      </span>
      <span className="row" style={{ gap: "0.4rem" }}>
        <button
          type="button"
          className={copied ? "icon-btn copied" : "icon-btn"}
          title={copied ? "Copied" : "Copy the stored value"}
          aria-label="Copy the stored value"
          onClick={() => setAsking(!asking)}
        >
          {copied ? <CheckIcon /> : <CopyIcon />}
        </button>
        {/* Asked, not assumed. */}
        <button
          type="button"
          className="icon-btn remove"
          title="Delete this credential"
          aria-label="Delete this credential"
          disabled={remove.busy}
          onClick={() => {
            if (window.confirm(CONFIRM[kind] ?? CONFIRM.default)) void remove.run();
          }}
        >
          <CrossIcon />
        </button>
      </span>
      {asking ? (
        <form
          className="stored-ask"
          onSubmit={(event) => {
            event.preventDefault();
            if (password) void copy.run();
          }}
        >
          <input
            type="password"
            value={password}
            placeholder="Account password to copy"
            autoComplete="current-password"
            autoFocus
            onChange={(event) => setPassword(event.target.value)}
          />
          <button type="submit" className="secondary" disabled={copy.busy || !password}>
            {copy.busy ? "…" : "Copy"}
          </button>
        </form>
      ) : null}
      <Error>{copy.error ?? remove.error}</Error>
    </div>
  );
}

function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="8" y="8" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="1.8" />
      <path d="M16 4H6a2 2 0 00-2 2v10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4.5 12.5l5 5 10-11" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Removal. A minus reads as "decrement"; a cross reads as "delete this". */
function CrossIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

function CredentialForm({
  kind,
  label,
  hint,
  placeholder,
  action = "Save",
  onDone,
}: {
  kind: string;
  label: string;
  hint: string;
  placeholder?: string;
  /** What the button says. Named after what it connects, not after what the form does. */
  action?: string;
  onDone: () => void;
}) {
  const [value, setValue] = useState("");
  const [warning, setWarning] = useState<string | null>(null);

  const save = useAction(async (allowUnvalidated: boolean) => {
    const result = await api.put<{ validated: boolean; detail: string }>(`/credentials/${kind}`, {
      value,
      allow_unvalidated: allowUnvalidated,
    });
    setValue("");
    // Stored but unchecked: the provider could not be reached.
    setWarning(result.validated ? null : result.detail);
    onDone();
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run(false);
      }}
    >
      <Field label={label} hint={hint}>
        <input
          value={value}
          type="password"
          required
          placeholder={placeholder}
          autoComplete="off"
          onChange={(event) => setValue(event.target.value)}
        />
      </Field>

      <Error>{save.error}</Error>
      {save.error ? (
        <p className="small">
          <button type="button" className="secondary" onClick={() => void save.run(true)}>
            Save it anyway. I know it is right
          </button>
        </p>
      ) : null}
      {warning ? <p className="small muted">Saved, but we could not check it: {warning}</p> : null}

      {/* Present and disabled, never invisible: a button that fades in only once you have typed
          leaves the field looking like it has no way to submit. */}
      <button type="submit" className="secondary" disabled={save.busy || !value}>
        {save.busy ? "Checking…" : action}
      </button>
    </form>
  );
}

type TelegramLink = components["schemas"]["TelegramLink"];

/** Telegram in one tap: a link opens the platform's bot, and pressing Start there connects it. */
export function TelegramStep({ onDone }: { onDone: () => void }) {
  const [status, setStatus] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  // Read again whenever this tab comes back into view: Start is pressed in Telegram, not here.
  const refresh = useCallback(() => {
    api
      .get<Account>("/account")
      .then((account) => setStatus(account.telegram_status))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);

  // A real link, fetched ahead: a window opened after an await is one browsers block.
  useEffect(() => {
    if (status === null || status === "connected") return;
    api
      .post<TelegramLink>("/telegram/link")
      .then((body) => setLink(body.url))
      .catch((error: unknown) =>
        setProblem(error instanceof ApiError ? error.detail : "Telegram is unavailable right now."),
      );
  }, [status]);

  const disconnect = useAction(async () => {
    await api.del("/telegram/link");
    refresh();
    onDone();
  });

  if (status === "connected") {
    return (
      <>
        <p className="small muted">Connected. New jobs that match you arrive in your Telegram chat.</p>
        <button className="secondary" disabled={disconnect.busy} onClick={() => void disconnect.run()}>
          Disconnect Telegram
        </button>
        <Error>{disconnect.error}</Error>
      </>
    );
  }
  return (
    <>
      <p className="small muted">
        {status === "broken"
          ? "Telegram stopped taking our messages. Connect it again:"
          : "Tap the button, then press Start in Telegram. That is all."}
      </p>
      {link ? (
        <a href={link} target="_blank" rel="noreferrer">
          <button>Connect Telegram</button>
        </a>
      ) : null}
      <Error>{problem}</Error>
    </>
  );
}

export function ClaudeStep({ onDone }: { onDone: () => void }) {
  const [kind, setKind] = useState<"claude_api" | "claude_oauth">("claude_api");
  // What makes the saved credential appear without a reload.
  const [saved, setSaved] = useState(0);
  const refresh = () => {
    setSaved((n) => n + 1);
    onDone();
  };
  return (
    <>
      <p className="small muted">
        Everything the model does runs on <strong>your</strong> credential, so you can see what it
        costs and we never hold a key that pays for strangers. Either kind works.
      </p>
      <Field label="What do you have?">
        <select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>
          <option value="claude_api">An API key (needs credit on it)</option>
          <option value="claude_oauth">A Claude subscription token</option>
        </select>
      </Field>
      {/* The subscription path is a button, not a paste field. */}
      {kind === "claude_oauth" ? (
        <>
          <ClaudeConnect onConnected={refresh} />
          <p className="small muted" style={{ marginTop: "1rem" }}>
            Already have a token from <code>claude setup-token</code>? Paste it instead.
          </p>
        </>
      ) : null}
      <CredentialForm
        key={kind}
        kind={kind}
        label={kind === "claude_api" ? "API key" : "OAuth token"}
        hint={
          kind === "claude_api"
            ? "From console.anthropic.com. It needs credit, we check that it works before saving."
            : "A token you already generated with the Claude Code CLI."
        }
        placeholder="sk-ant-…"
        onDone={refresh}
      />
      <Stored key={`stored-${kind}`} kind={kind} onChanged={refresh} version={saved} />
    </>
  );
}

export function ProxyStep({ onDone }: { onDone: () => void }) {
  const [saved, setSaved] = useState(0);
  const refresh = () => {
    setSaved((n) => n + 1);
    onDone();
  };
  return (
    <>
      <p className="small muted">
        LinkedIn is fetched through your own proxy and never a shared one, so one account's traffic
        can never be attributed to another's.
      </p>
      <CredentialForm
        kind="proxy"
        label="Proxy URL"
        hint="Including the username and password, e.g. http://user:pass@host:port. We fetch a page through it to check it works."
        placeholder="http://user:pass@host:port"
        onDone={refresh}
      />
      <Stored kind="proxy" onChanged={refresh} version={saved} />
    </>
  );
}
