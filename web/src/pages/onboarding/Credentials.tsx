/** Pages 4, 5 and 6 — the three credentials.
 *
 * Every block here reads in the same order: the box you type into, then what is already attached,
 * then the fold explaining how to get it. Someone arriving with the value in their clipboard never
 * has to scroll past instructions they do not need, and someone stuck finds the instructions where
 * the field ran out of answers.
 */
import { useCallback, useEffect, useState } from "react";

import { ClaudeConnect } from "../../components/ClaudeConnect";
import { api } from "../../api";
import { Error, Field, useAction } from "../../ui";

type StoredCredential = { kind: string; last_4: string | null; validated: boolean };

/** What each deletion actually costs, named. */
const CONFIRM: Record<string, string> = {
  telegram_bot:
    "Remove your Telegram bot token? Job delivery stops immediately and you will need to paste a "
    + "new token from BotFather to start it again.",
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
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="8" y="8" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="1.8" />
      <path d="M16 4H6a2 2 0 00-2 2v10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4.5 12.5l5 5 10-11" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Removal. A minus reads as "decrement"; a cross reads as "delete this". */
function CrossIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

/** A Telegram id that is already attached.
 *
 * The same row as `Stored`, but an id is not a secret: it is shown in full and copying one costs no
 * password. Deleting it does cost something, so that half is confirmed exactly the same way.
 */
function StoredId({
  value,
  what,
  confirm,
  onRemove,
}: {
  value: string;
  /** Named in the button labels, e.g. "chat ID". */
  what: string;
  confirm: string;
  onRemove: () => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const remove = useAction(onRemove);

  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2500);
  };

  return (
    <div className="stored">
      <span className="stored-value">{value}</span>
      <span className="row" style={{ gap: "0.4rem" }}>
        <button
          type="button"
          className={copied ? "icon-btn copied" : "icon-btn"}
          title={copied ? "Copied" : `Copy this ${what}`}
          aria-label={`Copy this ${what}`}
          onClick={() => void copy()}
        >
          {copied ? <CheckIcon /> : <CopyIcon />}
        </button>
        <button
          type="button"
          className="icon-btn remove"
          title={`Remove this ${what}`}
          aria-label={`Remove this ${what}`}
          disabled={remove.busy}
          onClick={() => {
            if (window.confirm(confirm)) void remove.run();
          }}
        >
          <CrossIcon />
        </button>
      </span>
      <Error>{remove.error}</Error>
    </div>
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

type TelegramState = {
  status: string;
  chat_id: string | null;
  channel_id: string | null;
  destination: "channel" | "chat" | null;
};

export function TelegramStep({ onDone }: { onDone: () => void }) {
  const [channel, setChannel] = useState("");
  const [channelStatus, setChannelStatus] = useState<string | null>(null);
  const [chat, setChat] = useState("");
  const [chatStatus, setChatStatus] = useState<string | null>(null);
  const [state, setState] = useState<TelegramState | null>(null);
  const [saved, setSaved] = useState(0);

  // What is actually configured, read back from the server rather than inferred from what this form last.
  const refresh = useCallback(() => {
    api
      .get<{ telegram?: TelegramState }>("/credentials")
      .then((body) => setState(body?.telegram ?? null))
      .catch(() => undefined);
  }, []);

  useEffect(() => refresh(), [refresh]);

  const reload = () => {
    refresh();
    setSaved((n) => n + 1);
    onDone();
  };

  const saveChat = useAction(async () => {
    const result = await api.put<{ detail: string }>("/credentials/telegram/chat", {
      chat_id: chat.trim(),
    });
    setChatStatus(result.detail);
    setChat("");
    reload();
  });

  const saveChannel = useAction(async () => {
    const result = await api.put<{ detail: string }>("/credentials/telegram/channel", {
      channel_id: channel.trim(),
    });
    setChannelStatus(result.detail);
    setChannel("");
    reload();
  });

  return (
    <>
      <CredentialForm
        kind="telegram_bot"
        label="Bot token"
        hint="From BotFather, it looks like 123456:AA…"
        placeholder="123456:AA…"
        action="Connect my bot"
        onDone={reload}
      />
      <Stored kind="telegram_bot" onChanged={reload} version={saved} />
      <details className="howto">
        <summary>How to create your bot (2 minutes)</summary>
        <ol className="small muted">
          <li>Open Telegram and search for <strong>@BotFather</strong> (blue tick).</li>
          <li>Press Start, then send the message <code>/newbot</code>.</li>
          <li>Give it any name, then any username ending in "bot".</li>
          <li>BotFather replies with a token that looks like <code>123456:AA…</code>, copy it and
              paste it above.</li>
          <li>Open your new bot's chat and press <strong>Start</strong>.</li>
        </ol>
      </details>

      {/* A field, not a status readout. */}
      <div style={{ marginTop: "1.5rem" }}>
        <p className="small muted">
          <strong>Required: your own chat with the bot.</strong> Commands and your finished CVs
          arrive here, separately from the job stream.
        </p>
        <Field
          label="Your chat ID"
          hint="A positive number, yours rather than the channel's. The channel ID starts with -100 and goes in the box further down."
        >
          <input
            value={chat}
            inputMode="numeric"
            placeholder="123456789"
            onChange={(event) => setChat(event.target.value)}
          />
        </Field>
        <Error>{saveChat.error}</Error>
        {chatStatus ? <p className="small" style={{ color: "var(--score)" }}>{chatStatus}</p> : null}
        <p>
          <button
            type="button"
            className="secondary"
            disabled={saveChat.busy || !chat.trim()}
            onClick={() => void saveChat.run()}
          >
            {saveChat.busy ? "Checking…" : "Connect my chat"}
          </button>
        </p>
        {state?.chat_id ? (
          <StoredId
            value={state.chat_id}
            what="chat ID"
            confirm={
              "Remove your chat ID? Commands and your finished CVs have nowhere to arrive until "
              + "you connect a chat again."
            }
            onRemove={async () => {
              await api.del("/credentials/telegram/chat");
              setChatStatus(null);
              reload();
            }}
          />
        ) : null}
        <details className="howto">
          <summary>How to find your chat ID (1 minute)</summary>
          <ol className="small muted">
            <li>Open your new bot in Telegram and press <strong>Start</strong>.</li>
            <li>Search for <strong>@getidsbot</strong> and press Start there too.</li>
            <li>It replies with your details. The number next to <strong>Id</strong> is yours,
                a positive number like <code>123456789</code>.</li>
            <li>Paste it above. We send a test message to prove the bot can reach you.</li>
          </ol>
        </details>
      </div>

      <div style={{ marginTop: "2rem" }}>
        <p className="small muted">
          <strong>Required: a channel for your job stream.</strong> Jobs land in a channel you can
          scroll like a feed, and the bot chat stays for commands and your finished CVs. Without it
          there is nowhere for matches to go.
        </p>
        <Field
          label="Channel ID"
          hint="Starts with -100. Every job goes to this one channel, if you already have separate channels set up, this replaces that split."
        >
          <input
            value={channel}
            placeholder="-1001234567890"
            onChange={(event) => setChannel(event.target.value)}
          />
        </Field>
        <Error>{saveChannel.error}</Error>
        {channelStatus ? (
          <p className="small" style={{ color: "var(--score)" }}>
            {channelStatus}
          </p>
        ) : null}
        <p>
          <button
            type="button"
            className="secondary"
            disabled={saveChannel.busy || !channel.trim()}
            onClick={() => void saveChannel.run()}
          >
            {saveChannel.busy ? "Testing…" : "Connect channel"}
          </button>
        </p>
        {state?.channel_id ? (
          <StoredId
            value={state.channel_id}
            what="channel ID"
            confirm={
              "Remove your channel? Jobs go back to your bot chat, mixed in with commands and your "
              + "finished CVs."
            }
            onRemove={async () => {
              await api.del("/credentials/telegram/channel");
              setChannelStatus(null);
              reload();
            }}
          />
        ) : null}
        <details className="howto">
          <summary>How to find your channel ID (2 minutes)</summary>
          <ol className="small muted">
            <li>In Telegram, create a channel (New message → New channel). Private is fine.</li>
            <li>Open the channel's settings → Administrators → Add admin → pick <strong>your
                bot</strong>, and allow it to post messages.</li>
            <li>Post any message in the channel, "hello" works.</li>
            <li>Forward that message to <strong>@getidsbot</strong>. It replies with a "forwarded
                from" ID starting with <code>-100</code>.</li>
            <li>Paste that ID above. We send a test message to prove it works, you will see it in
                the channel.</li>
          </ol>
        </details>
      </div>
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
