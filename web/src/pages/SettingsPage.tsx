/**
 * Everything onboarding collected, plus the two things only this page can do: withdraw consent, and
 * delete the account.
 *
 * **Deletion asks for the password, not a checkbox.** It is irreversible and it unwinds an account
 * somebody spent real money building, so a stolen session cookie must not be enough.
 */
import { useEffect, useState } from "react";

import { api, type Account } from "../api";
import { Card, Error, Field, useAction } from "../ui";

export function SettingsPage({ onSignedOut }: { onSignedOut: () => void }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [password, setPassword] = useState("");
  const [confirming, setConfirming] = useState(false);

  const reload = () => api.get<Account>("/account").then(setAccount).catch(() => undefined);
  useEffect(() => {
    void reload();
  }, []);

  const signOut = useAction(async () => {
    await api.post("/auth/logout");
    onSignedOut();
  });

  const withdraw = useAction(async () => {
    await api.del("/account/consent");
    await reload();
  });

  const remove = useAction(async () => {
    await api.del("/account", { password });
    onSignedOut();
  });

  if (!account) return <p className="muted">Loading…</p>;

  return (
    <>
      <h1>Settings</h1>

      <Card title="Account">
        <table>
          <tbody>
            <tr>
              <td>Email</td>
              <td>{account.email}</td>
            </tr>
            <tr>
              <td>Name</td>
              <td>{account.full_name ?? "—"}</td>
            </tr>
            <tr>
              <td>Telegram</td>
              <td>{account.telegram_status}</td>
            </tr>
            <tr>
              <td>Feed</td>
              <td>{account.active ? "running" : "paused"}</td>
            </tr>
            <tr>
              <td>Plan</td>
              <td>{account.plan}</td>
            </tr>
          </tbody>
        </table>
        {account.links.length ? (
          <p className="small muted">
            {account.links.map((link) => `${link.platform}: ${link.url}`).join(" · ")}
          </p>
        ) : null}
      </Card>

      <Card title="Your data">
        <p className="small muted">
          We send your document text to Anthropic on your own credential when you process it, and
          nowhere else. <a href="#/privacy">The full list of who sees what</a>.
        </p>
        {account.consent_version ? (
          <>
            <Error>{withdraw.error}</Error>
            <button className="secondary" disabled={withdraw.busy} onClick={() => void withdraw.run()}>
              Withdraw my agreement
            </button>
            <p className="small muted">
              Processing becomes unavailable. Your existing profile is unchanged — deleting your
              account is what removes everything.
            </p>
          </>
        ) : (
          <p className="small muted">You have not agreed to processing yet.</p>
        )}
      </Card>

      <Card title="Delete everything">
        <p className="small muted">
          Your profile, documents, labels, model and credentials, permanently. Your encryption key is
          destroyed in the same request, so anything in a backup taken minutes ago is unreadable
          rather than merely deleted. This cannot be undone.
        </p>
        {confirming ? (
          <>
            <Field label="Your password">
              <input
                type="password"
                value={password}
                autoComplete="current-password"
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
            <Error>{remove.error}</Error>
            <div className="row">
              <button
                className="danger"
                disabled={remove.busy || !password}
                onClick={() => void remove.run()}
              >
                {remove.busy ? "Deleting…" : "Delete my account permanently"}
              </button>
              <button className="secondary" onClick={() => setConfirming(false)}>
                Keep it
              </button>
            </div>
          </>
        ) : (
          <button className="danger" onClick={() => setConfirming(true)}>
            Delete my account
          </button>
        )}
      </Card>

      <p>
        <button className="secondary" disabled={signOut.busy} onClick={() => void signOut.run()}>
          Sign out
        </button>
      </p>
    </>
  );
}
