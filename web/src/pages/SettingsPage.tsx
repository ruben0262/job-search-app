/** Settings: alerts, AI writing and your account. Help moved to the "?" beside the bell. */
import { useState } from "react";

import { AiAccounts } from "../aiaccounts";
import { Alerts } from "../alerts";
import { api, type Ready, type Schema } from "../api";
import { useKept } from "../kept";
import { Card, Error, Field, Prompt, useAction } from "../ui";

type Account = Schema<"AccountOut">;

/** What Settings reads, as `kept.ts` keeps it: shown at once on every visit. */
export const ACCOUNT = "/account";

export function SettingsPage({ dashboard, onChanged, onSignedOut }: {
  dashboard: Ready;
  onChanged: () => void;
  onSignedOut: () => void;
}) {
  const { data: account, reload: reloadAccount } = useKept<Account>(ACCOUNT);

  const reload = () => {
    void reloadAccount();
  };
  // A pause changes the jobs page's banner, which is the dashboard's.
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

  return (
    <>
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

      <Card title="Your AI accounts">
        <AiAccounts />
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
        <Prompt title="Delete your account?" onClose={() => setAsking(false)}>
            <p className="small muted">
              Your documents, words and keys are erased and your encryption key destroyed. This cannot
              be undone.
            </p>
            <Field label="Type your password to confirm">
              <input type="password" value={password} autoFocus autoComplete="current-password"
                     onChange={(event) => setPassword(event.target.value)}
                     onKeyDown={(event) => {
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
        </Prompt>
      ) : null}
    </>
  );
}
