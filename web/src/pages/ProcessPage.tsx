/**
 * Process data, and the confirmation step.
 *
 * **The confirmation is the point of this screen, not a formality.** Every entry in the "not a fit"
 * list becomes a hard gate — a wrong one silently suppresses jobs the person wanted, for as long as
 * the profile lasts — so the draft is shown in full and edited here before anything uses it. The
 * server forces `confirmed` itself, so posting the draft back untouched cannot confirm on somebody's
 * behalf; this form exists so they actually read it.
 *
 * **The warning names a number.** "About 3,310 jobs, roughly $5.96 on your own key" is something a
 * person weighs. "Are you sure?" is something they click past.
 */
import { useEffect, useState } from "react";

import { api, type ProcessState } from "../api";
import { Card, Error, Field, useAction } from "../ui";

type Draft = {
  identity: { name: string; location?: string | null };
  not_list: string[];
  exclusions: string[];
  conflicts: { field: string; value_a: string; value_b: string }[];
  [key: string]: unknown;
};

export function ProcessPage({ onChanged }: { onChanged: () => void }) {
  const [state, setState] = useState<ProcessState | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [notList, setNotList] = useState("");
  const [consented, setConsented] = useState<boolean | null>(null);

  const reload = async () => {
    const next = await api.get<ProcessState>("/process/state");
    setState(next);
    if (next.has_draft) {
      const body = await api.get<{ draft: Draft }>("/process/draft");
      setDraft(body.draft);
      setNotList(body.draft.not_list.join("\n"));
    } else {
      setDraft(null);
    }
    const consent = await api.get<{ granted: boolean }>("/account/consent");
    setConsented(consent.granted);
  };

  useEffect(() => {
    void reload().catch(() => undefined);
  }, []);

  const start = useAction(async () => {
    await api.post("/process");
    await reload();
    onChanged();
  });

  const confirm = useAction(async () => {
    await api.post("/process/confirm", {
      ...draft,
      not_list: notList
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
    });
    await reload();
    onChanged();
  });

  const discard = useAction(async () => {
    await api.del("/process/draft");
    await reload();
  });

  if (!state) return <p className="muted">Loading…</p>;

  if (draft) {
    return (
      <>
        <p className="kicker">Review</p>
        <h1>Review your profile</h1>
        <p className="muted">
          Nothing here is used until you agree to it. Read the last section especially carefully.
        </p>

        <Card title={draft.identity.name}>
          <p className="small muted">{draft.identity.location ?? "No location recorded"}</p>
        </Card>

        {draft.conflicts.length ? (
          <Card title="We could not work these out">
            {draft.conflicts.map((conflict, index) => (
              <p key={index} className="small">
                <strong>{conflict.field}:</strong> {conflict.value_a}, or, {conflict.value_b}
              </p>
            ))}
          </Card>
        ) : null}

        <Card title="What you are not a fit for">
          <p className="small muted">
            Each line here is a <strong>hard rule</strong>: jobs whose core requirement is one of
            these will never be shown to you. Delete anything you disagree with, a wrong line
            silently hides work you would have wanted.
          </p>
          <Field label="One per line">
            <textarea value={notList} onChange={(event) => setNotList(event.target.value)} />
          </Field>
        </Card>

        <Error>{confirm.error ?? discard.error}</Error>
        <div className="row">
          <button disabled={confirm.busy} onClick={() => void confirm.run()}>
            {confirm.busy ? "Saving…" : "This is right, use it"}
          </button>
          <button className="danger" disabled={discard.busy} onClick={() => void discard.run()}>
            Throw it away
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <p className="kicker">Processing</p>
      <h1>Your profile</h1>
      <p>{state.message}</p>

      {consented === false ? <Consent onGranted={() => void reload()} /> : null}

      <Card title="Before you start">
        <p>{state.warning.text}</p>
        <p className="small muted">
          {state.documents} document{state.documents === 1 ? "" : "s"} will be read. This is the one
          step that spends your Claude credential, and it takes a few minutes.
        </p>
        <Error>{start.error}</Error>
        <button
          disabled={start.busy || consented === false || state.action === "upload"}
          onClick={() => void start.run()}
        >
          {start.busy ? "Queued…" : "Process my documents"}
        </button>
      </Card>
    </>
  );
}

/** The consent screen, shown at the moment it is owed: nothing leaves this server until the button
 * below is pressed, so asking earlier would be asking about something that had not happened. */
function Consent({ onGranted }: { onGranted: () => void }) {
  const [disclosure, setDisclosure] = useState<{
    version: string;
    headline: string;
    body: string;
    processors: { name: string; purpose: string; data: string; location: string }[];
  } | null>(null);

  useEffect(() => {
    void api
      .get<{ disclosure: typeof disclosure }>("/account/consent")
      .then((body) => setDisclosure(body.disclosure))
      .catch(() => undefined);
  }, []);

  const grant = useAction(async () => {
    await api.post("/account/consent", { version: disclosure?.version });
    onGranted();
  });

  if (!disclosure) return null;

  return (
    <Card title={disclosure.headline}>
      {disclosure.body.split("\n\n").map((paragraph, index) => (
        <p key={index} className="small">
          {paragraph.replaceAll("**", "")}
        </p>
      ))}
      <table>
        <tbody>
          {disclosure.processors.map((processor) => (
            <tr key={processor.name}>
              <td>
                <strong>{processor.name}</strong>
                <div className="small muted">{processor.location}</div>
              </td>
              <td className="small">
                {processor.purpose}
                <div className="muted">{processor.data}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Error>{grant.error}</Error>
      <button disabled={grant.busy} onClick={() => void grant.run()}>
        I understand, go ahead
      </button>
    </Card>
  );
}
