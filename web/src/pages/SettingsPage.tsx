/** Settings: alerts, AI writing, and your account. */
import { useEffect, useState } from "react";

import { Alerts } from "../alerts";
import { api, type Ready, type Schema } from "../api";
import { ClaudeConnect } from "../components/ClaudeConnect";
import { Card, Error, Field, useAction } from "../ui";

type Account = Schema<"AccountOut">;
type Stored = Schema<"StoredCredential">;

// The last answer, kept across mounts to kill the split-second "Loading…" on every visit.
let cachedAccount: Account | null = null;

/** Forgotten on sign-out, or the next account flashes this one's details. */
export function clearAccountCache(): void {
  cachedAccount = null;
}

/** Your own key for AI-written documents: what each box is called and where the key comes from. */
const KEYS = {
  claude_api: ["Claude key", "From console.anthropic.com, with credit on it.", "sk-ant-…"],
  claude_oauth: ["Claude subscription", "Connect with the button, or paste a token from claude setup-token.",
                 "sk-ant-oat…"],
  gemini_api: ["Gemini key", "From aistudio.google.com, on a project with billing turned on.", "AIza…"],
  openai_api: ["OpenAI key", "From platform.openai.com.", "sk-…"],
} as const;

export function SettingsPage({ dashboard, onChanged, onSignedOut }: {
  dashboard: Ready;
  onChanged: () => void;
  onSignedOut: () => void;
}) {
  const [account, setAccount] = useState<Account | null>(cachedAccount);
  const [stored, setStored] = useState<Stored[]>([]);
  const [provider, setProvider] = useState<keyof typeof KEYS>("claude_api");

  const reload = () => {
    api.get<Account>("/account").then((fresh) => {
      cachedAccount = fresh;
      setAccount(fresh);
    }).catch(() => undefined);
    api.get<Schema<"CredentialList">>("/credentials").then((body) => setStored(body.credentials))
      .catch(() => undefined);
  };
  useEffect(reload, []);
  // A key changes who pays for AI and a pause changes the jobs page's banner: both are the dashboard's.
  const dashboardChanged = () => {
    reload();
    onChanged();
  };

  const pause = useAction(async () => {
    await api.patch("/account", { active: !account?.active });
    dashboardChanged();
  });
  const withdraw = useAction(async () => {
    await api.del("/account/consent");
    reload();
  });
  const signOut = useAction(async () => {
    await api.post("/auth/logout");
    onSignedOut();
  });

  if (!account) return <p className="muted">Loading your settings…</p>;
  const [label, hint, placeholder] = KEYS[provider];
  const { ai } = dashboard;

  return (
    <>
      <p className="kicker">Settings</p>
      <h1>Settings</h1>

      <Card title="Alerts">
        <p className="small muted">
          Optional. New jobs that match your search reach you as they are found, or in one email each morning.
        </p>
        <Alerts />
        <div className="channel">
          <span>
            <span className="label">{account.active ? "Alerts are on" : "Alerts are paused"}</span>
            <span className="hint">
              {account.active
                ? "Pausing stops alerts to Telegram and to this browser. Your jobs page keeps filling up."
                : "Nothing is sent to your phone. Your jobs page keeps filling up as usual."}
            </span>
          </span>
          <button type="button" className="secondary" disabled={pause.busy} onClick={() => void pause.run()}>
            {account.active ? "Pause alerts" : "Turn alerts back on"}
          </button>
        </div>
        <Error>{pause.error}</Error>
      </Card>

      <Card title="AI writing">
        <p className="small muted">Instant CVs and cover letters are free and made right here.</p>
        <p>
          {!ai.profile ? "AI-written CVs and cover letters are not ready for new accounts yet."
            : ai.payer === "own" ? "Your own key is saved: AI writing is unlimited."
              : ai.payer === "platform" ? `AI requests left today: ${ai.left} of ${ai.per_day}`
                : ai.per_day > 0 && ai.left === 0
                  ? `You have used today's ${ai.per_day} AI requests. More tomorrow, or use your own key.`
                  : "Save your own key below to have AI write them."}
        </p>
        <Field label="Use your own key — unlimited">
          <select value={provider} onChange={(event) => setProvider(event.target.value as typeof provider)}>
            {Object.entries(KEYS).map(([kind, [name]]) => (
              <option key={kind} value={kind}>{name}</option>
            ))}
          </select>
        </Field>
        {provider === "claude_oauth" ? <ClaudeConnect onConnected={dashboardChanged} /> : null}
        <Credential key={provider} kind={provider} label={label} hint={hint} placeholder={placeholder}
                    stored={stored} onChanged={dashboardChanged} />
        {account.consent_version ? (
          <p className="small muted">
            You agreed that AI-written documents may use your profile.{" "}
            <button type="button" className="linklike" disabled={withdraw.busy} onClick={() => void withdraw.run()}>
              Withdraw
            </button>
          </p>
        ) : null}
        <Error>{withdraw.error}</Error>
      </Card>

      <Card title="Your account">
        <p>{account.email}</p>
        <Password onChanged={onSignedOut} />
        <div className="row">
          <button type="button" className="secondary" disabled={signOut.busy} onClick={() => void signOut.run()}>
            Sign out
          </button>
          <DeleteAccount onDeleted={onSignedOut} />
        </div>
        <Error>{signOut.error}</Error>
      </Card>

      <p className="small">
        <a href="#/privacy">Who sees your data</a>
        {dashboard.is_admin ? <> · <a href="#/admin">Platform</a></> : null}
      </p>
    </>
  );
}

/** One credential: a box to save it, and what is saved, which can be removed. */
function Credential({ kind, label, hint, placeholder, stored, onChanged }: {
  kind: string;
  label: string;
  hint: string;
  placeholder: string;
  stored: Stored[];
  onChanged: () => void;
}) {
  const [value, setValue] = useState("");
  const [unchecked, setUnchecked] = useState<string | null>(null);
  const save = useAction(async (anyway: boolean) => {
    const result = await api.put<Schema<"Saved">>(`/credentials/${kind}`, { value, allow_unvalidated: anyway });
    setValue("");
    // Stored but unchecked: the provider could not be reached to try it.
    setUnchecked(result.validated ? null : result.detail);
    onChanged();
  });
  const remove = useAction(async () => {
    await api.del(`/credentials/${kind}`);
    onChanged();
  });
  const saved = stored.find((each) => each.kind === kind);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run(false);
      }}
    >
      <Field label={label} hint={hint}>
        <input type="password" value={value} placeholder={placeholder} autoComplete="off"
               onChange={(event) => setValue(event.target.value)} />
      </Field>
      <Error>{save.error}</Error>
      {save.error ? (
        <button type="button" className="secondary small" onClick={() => void save.run(true)}>
          Save it anyway, I know it is right
        </button>
      ) : null}
      {unchecked ? <p className="small muted">Saved, but we could not check it: {unchecked}</p> : null}
      <div className="row">
        <button type="submit" className="secondary" disabled={save.busy || !value}>
          {save.busy ? "Checking…" : "Save"}
        </button>
        {saved ? (
          <span className="saved-key">
            Saved: ••••{saved.last_4 ?? ""}
            <button type="button" className="linklike" disabled={remove.busy}
                    onClick={() => {
                      if (window.confirm(`Remove your ${label.toLowerCase()}?`)) void remove.run();
                    }}>
              Remove
            </button>
          </span>
        ) : null}
      </div>
      <Error>{remove.error}</Error>
    </form>
  );
}

function Password({ onChanged }: { onChanged: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  const change = useAction(async () => {
    await api.put("/account/password", { current_password: current, new_password: next });
    setDone(true);
    // A beat to read the sentence before the reload lands on the sign-in page.
    window.setTimeout(onChanged, 1500);
  });

  if (done) return <p className="small muted">Changed. Sign back in with your new password.</p>;
  return (
    <details className="fold-plain">
      <summary>Change your password</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void change.run();
        }}
      >
        <Field label="Current password">
          <input type="password" value={current} autoComplete="current-password"
                 onChange={(event) => setCurrent(event.target.value)} />
        </Field>
        <Field label="New password" hint="At least 9 characters. Longer beats complicated.">
          <input type="password" value={next} autoComplete="new-password"
                 onChange={(event) => setNext(event.target.value)} />
        </Field>
        <Error>{change.error}</Error>
        <p className="small muted">This signs you out everywhere, here too.</p>
        <button type="submit" className="secondary" disabled={change.busy || !current || !next}>
          {change.busy ? "Changing…" : "Change password"}
        </button>
      </form>
    </details>
  );
}

function DeleteAccount({ onDeleted }: { onDeleted: () => void }) {
  const [asking, setAsking] = useState(false);
  const [password, setPassword] = useState("");
  const remove = useAction(async () => {
    await api.del("/account", { password });
    onDeleted();
  });

  return (
    <>
      <button type="button" className="danger" onClick={() => setAsking(true)}>
        Delete my account
      </button>
      {asking ? (
        <div className="modal-backdrop" onClick={() => setAsking(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <h2>Delete your account?</h2>
            <p className="small muted">
              Your documents, words and keys are erased and your encryption key destroyed. This cannot
              be undone.
            </p>
            <Field label="Type your password to confirm">
              <input type="password" value={password} autoFocus autoComplete="current-password"
                     onChange={(event) => setPassword(event.target.value)}
                     onKeyDown={(event) => {
                       if (event.key === "Escape") setAsking(false);
                       if (event.key === "Enter" && password && !remove.busy) void remove.run();
                     }} />
            </Field>
            <Error>{remove.error}</Error>
            <div className="row">
              <button type="button" className="secondary" onClick={() => setAsking(false)}>
                Keep it
              </button>
              <button type="button" className="danger" disabled={remove.busy || !password}
                      onClick={() => void remove.run()}>
                {remove.busy ? "Deleting…" : "Delete it for good"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
