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

      <DeliveryCard interval={account.notify_interval_min} onChanged={() => void reload()} />

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

/** The two delivery knobs, side by side because people confuse them and the fix is words:
 *  the interval is when we check, the freshness is what is still worth sending when we do. */
function DeliveryCard({ interval, onChanged }: { interval: number; onChanged: () => void }) {
  const [minutes, setMinutes] = useState(interval);
  const [hours, setHours] = useState<number | null>(null);
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void api
      .get<{ config: { sections: { posted_within_hours: number }[] } | null }>("/onboarding/config")
      .then((stored) => {
        if (stored.config) {
          setConfig(stored.config as unknown as Record<string, unknown>);
          setHours(stored.config.sections[0]?.posted_within_hours ?? 24);
        }
      })
      .catch(() => undefined);
  }, []);

  const save = useAction(async () => {
    setSaved(false);
    if (minutes !== interval) {
      await api.patch("/account", { notify_interval_min: minutes });
    }
    if (config && hours !== null) {
      const sections = (config.sections as { posted_within_hours: number }[]).map(
        (section, index) => (index === 0 ? { ...section, posted_within_hours: hours } : section),
      );
      await api.put("/onboarding/config", { ...config, sections });
    }
    setSaved(true);
    onChanged();
  });

  return (
    <Card title="Delivery">
      <Field
        label="How often we message you"
        hint="How often Telegram is checked for something new to send. More often means fresher, smaller batches."
      >
        <select value={minutes} onChange={(event) => setMinutes(Number(event.target.value))}>
          <option value={15}>Every 15 minutes</option>
          <option value={30}>Every 30 minutes</option>
          <option value={60}>Every hour</option>
          <option value={120}>Every 2 hours</option>
          <option value={240}>Every 4 hours</option>
          <option value={480}>Twice a day</option>
          <option value={1440}>Once a day</option>
        </select>
      </Field>

      {hours !== null ? (
        <Field
          label="How fresh a job has to be"
          hint="Jobs older than this are never sent. Tighter means fewer, earlier applications — a job posted an hour ago has 5 applicants, not 500."
        >
          <select value={hours} onChange={(event) => setHours(Number(event.target.value))}>
            <option value={1}>Posted in the last hour</option>
            <option value={2}>Posted in the last 2 hours</option>
            <option value={4}>Posted in the last 4 hours</option>
            <option value={6}>Posted in the last 6 hours</option>
            <option value={12}>Posted in the last 12 hours</option>
            <option value={24}>Posted in the last day</option>
            <option value={72}>Posted in the last 3 days</option>
            <option value={168}>Posted in the last week</option>
          </select>
        </Field>
      ) : null}

      <Error>{save.error}</Error>
      <div className="row">
        <button disabled={save.busy} onClick={() => void save.run()}>
          {save.busy ? "Saving…" : "Save"}
        </button>
        {saved ? <span className="small" style={{ color: "var(--score)" }}>Saved.</span> : null}
      </div>
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
          Changed. Every session is signed out, including this one — sign back in with the new
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
        <Field label="New password" hint="At least 12 characters. Longer beats complicated.">
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
