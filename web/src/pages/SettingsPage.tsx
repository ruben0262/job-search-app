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

// The last answer, kept across mounts to kill the split-second "Loading…" on every visit.
let cachedAccount: Account | null = null;

/** Forgotten on sign-out, or the next account flashes this one's details. */
export function clearAccountCache(): void {
  cachedAccount = null;
}

export function SettingsPage({ onSignedOut }: { onSignedOut: () => void }) {
  const [account, setAccount] = useState<Account | null>(cachedAccount);
  const [password, setPassword] = useState("");
  const [confirming, setConfirming] = useState(false);

  const reload = () =>
    api
      .get<Account>("/account")
      .then((fresh) => {
        cachedAccount = fresh;
        setAccount(fresh);
      })
      .catch(() => undefined);
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
      <p className="kicker">Your account</p>
      <h1>Settings</h1>

      <Card title="Account">
        <table className="kv">
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
              <td>Plan</td>
              <td>{account.plan}</td>
            </tr>
          </tbody>
        </table>
      </Card>

      <DeliveryCard
        interval={account.notify_interval_min}
        active={account.active}
        onChanged={() => void reload()}
      />

      <PasswordCard onChanged={onSignedOut} />

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
              Processing becomes unavailable. Your existing profile is unchanged, deleting your
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

/** The delivery knobs, side by side because people confuse them and the fix is words:
 *  the switch is whether messages arrive at all, the interval is when we check, the freshness is
 *  what is still worth sending when we do. */
function DeliveryCard({
  interval,
  active,
  onChanged,
}: {
  interval: number;
  active: boolean;
  onChanged: () => void;
}) {
  const toggle = useAction(async () => {
    await api.patch("/account", { active: !active });
    onChanged();
  });

  return (
    <Card title="Delivery">
      <div style={{ marginBottom: "1.25rem" }}>
        <span>
          <span className="label" style={{ marginBottom: 0 }}>
            Job messages are {active ? "on" : "off"}
          </span>
          <span className="hint" style={{ marginBottom: 0 }}>
            {active
              ? "Pausing stops Telegram messages. Collection and scoring keep running, and the bot stays on, resuming picks up where you left off."
              : "Nothing is being sent. Everything else keeps running, so turning it back on delivers a warm feed."}
          </span>
        </span>
        <button style={{ marginTop: "1.15rem" }}
          className={active ? "secondary" : ""}
          disabled={toggle.busy}
          onClick={() => void toggle.run()}
        >
          {toggle.busy ? "…" : active ? "Pause messages" : "Start messages"}
        </button>
      </div>
      <Error>{toggle.error}</Error>

      {/* **The cadence and the freshness window are the platform's** (2026-08-01), so they are
          not here any more, a user choosing "every four hours" was choosing to hear about a
          two-hour-old job, which is the opposite of what this product is. What remains is the one
          real choice: whether messages are sent at all. */}
      <p className="facts">
        Jobs are sent as soon as they are found, every {interval} minutes, when there is something
        new that matches you. How recent a posting has to be is set per job board by the platform.
      </p>
    </Card>
  );
}

/** Change the password. Success signs you out everywhere — including here — because if the
 *  password is being changed for the reason passwords usually are, a session the thief already
 *  holds must die with it. The server enforces that; this just says it out loud beforehand. */
function PasswordCard({ onChanged }: { onChanged: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);

  const change = useAction(async () => {
    await api.put("/account/password", { current_password: current, new_password: next });
    setDone(true);
    // Give the sentence a beat to be read before the reload lands on the sign-in page.
    window.setTimeout(onChanged, 1500);
  });

  if (done) {
    return (
      <Card title="Password">
        <p className="small muted">
          Changed. Every session is signed out, including this one, sign back in with the new
          password.
        </p>
      </Card>
    );
  }

  return (
    <Card title="Password">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void change.run();
        }}
      >
        <Field label="Current password">
          <input
            type="password"
            value={current}
            autoComplete="current-password"
            required
            onChange={(event) => setCurrent(event.target.value)}
          />
        </Field>
        <Field label="New password" hint="At least 9 characters. Longer beats complicated.">
          <input
            type="password"
            value={next}
            autoComplete="new-password"
            required
            onChange={(event) => setNext(event.target.value)}
          />
        </Field>
        <Error>{change.error}</Error>
        <p className="small muted">Changing it signs you out on every device, this one included.</p>
        <button type="submit" disabled={change.busy || !current || !next}>
          {change.busy ? "Changing…" : "Change password"}
        </button>
      </form>
    </Card>
  );
}
