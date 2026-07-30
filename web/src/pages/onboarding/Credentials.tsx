/**
 * Pages 4, 5 and 6 — the three credentials.
 *
 * **Write-only, everywhere.** No response ever contains a credential value, so there is nothing to
 * prefill and no "show" toggle to build. A saved credential shows as saved and nothing more.
 *
 * **The Telegram chat id is captured for the user.** It is the step that confuses people more than
 * the rest of setup put together, so the form asks for the bot token, then waits while they press
 * Start on their phone and reads the id itself.
 */
import { useState } from "react";

import { api } from "../../api";
import { Error, Field, useAction } from "../../ui";

function CredentialForm({
  kind,
  label,
  hint,
  placeholder,
  onDone,
}: {
  kind: string;
  label: string;
  hint: string;
  placeholder?: string;
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
    // Stored but unchecked: the provider could not be reached. Not a failure — an outage on their
    // side must not stop somebody finishing setup — but it is worth saying so it is not a surprise
    // at three in the morning.
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
            Save it anyway — I know it is right
          </button>
        </p>
      ) : null}
      {warning ? <p className="small muted">Saved, but we could not check it: {warning}</p> : null}

      <button type="submit" className={value ? "advance ready" : "advance"} disabled={save.busy}>
        {save.busy ? "Checking…" : "Save"}
      </button>
    </form>
  );
}

export function TelegramStep({ onDone }: { onDone: () => void }) {
  const [status, setStatus] = useState<string | null>(null);
  const [channel, setChannel] = useState("");
  const [channelStatus, setChannelStatus] = useState<string | null>(null);

  const connect = useAction(async () => {
    const result = await api.post<{ status: string; detail?: string }>("/credentials/telegram/connect");
    setStatus(result.status === "connected" ? null : (result.detail ?? "Still waiting."));
    onDone();
  });

  const saveChannel = useAction(async () => {
    const result = await api.put<{ detail: string }>("/credentials/telegram/channel", {
      channel_id: channel.trim(),
    });
    setChannelStatus(result.detail);
    onDone();
  });

  return (
    <>
      <details className="howto">
        <summary>How to create your bot (2 minutes)</summary>
        <ol className="small muted">
          <li>Open Telegram and search for <strong>@BotFather</strong> (blue tick).</li>
          <li>Press Start, then send the message <code>/newbot</code>.</li>
          <li>Give it any name, then any username ending in "bot".</li>
          <li>BotFather replies with a token that looks like <code>123456:AA…</code> — copy it and
              paste it below.</li>
          <li>Open your new bot's chat and press <strong>Start</strong>, then use the "I pressed
              Start" button underneath.</li>
        </ol>
      </details>

      <CredentialForm
        kind="telegram_bot"
        label="Bot token"
        hint="From BotFather. We read your chat id ourselves — you never have to find it."
        placeholder="123456:AA…"
        onDone={onDone}
      />

      <p style={{ marginTop: "1rem" }}>
        <button className="secondary" disabled={connect.busy} onClick={() => void connect.run()}>
          {connect.busy ? "Looking…" : "I pressed Start"}
        </button>
      </p>
      <Error>{connect.error}</Error>
      {status ? <p className="small muted">{status}</p> : null}

      <div style={{ marginTop: "2rem" }}>
        <p className="small muted">
          <strong>Optional: a channel for your job stream.</strong> Jobs land in a channel you can
          scroll like a feed, and the bot chat stays for commands and your finished CVs.
        </p>
        <details className="howto">
          <summary>How to find your channel ID (2 minutes)</summary>
          <ol className="small muted">
            <li>In Telegram, create a channel (New message → New channel). Private is fine.</li>
            <li>Open the channel's settings → Administrators → Add admin → pick <strong>your
                bot</strong>, and allow it to post messages.</li>
            <li>Post any message in the channel — "hello" works.</li>
            <li>Forward that message to <strong>@getidsbot</strong>. It replies with a "forwarded
                from" ID starting with <code>-100</code>.</li>
            <li>Paste that ID below. We send a test message to prove it works — you will see it in
                the channel.</li>
          </ol>
        </details>
        <Field label="Channel ID" hint="Starts with -100. Leave empty to keep everything in the bot chat.">
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
        <button
          type="button"
          className="secondary"
          disabled={saveChannel.busy || !channel.trim()}
          onClick={() => void saveChannel.run()}
        >
          {saveChannel.busy ? "Testing…" : "Connect channel"}
        </button>
      </div>
    </>
  );
}

export function ClaudeStep({ onDone }: { onDone: () => void }) {
  const [kind, setKind] = useState<"claude_api" | "claude_oauth">("claude_api");
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
      <CredentialForm
        key={kind}
        kind={kind}
        label={kind === "claude_api" ? "API key" : "OAuth token"}
        hint={
          kind === "claude_api"
            ? "From console.anthropic.com. It needs credit — we check that it works before saving."
            : "From your Claude subscription."
        }
        placeholder="sk-ant-…"
        onDone={onDone}
      />
    </>
  );
}

export function ProxyStep({ onDone }: { onDone: () => void }) {
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
        onDone={onDone}
      />
    </>
  );
}
