/**
 * Pages 2 and 3.
 *
 * **Uploads keep the text and discard the file.** The server extracts it in the request and the
 * bytes never reach disk — worth saying on the form, because "we store your CV" and "we store what
 * your CV says" are different promises to somebody handing over their employment history.
 *
 * The history page is a textarea rather than an upload: nobody has a long-form "everything I have
 * done" document lying around. They write one, and it is stored as a document like any other so
 * the processing step has a single input path.
 */
import { useEffect, useState } from "react";

import { api, type DocumentList } from "../../api";
import { Error, Field, useAction } from "../../ui";

const KINDS = [
  ["cv", "CV"],
  ["cover_letter", "Cover letter"],
  ["reference", "Reference"],
  ["achievement", "Achievement"],
  ["diploma", "Diploma"],
  ["linkedin", "LinkedIn export"],
  ["other", "Something else"],
] as const;

export function DocumentsStep({ onDone }: { onDone: () => void }) {
  const [list, setList] = useState<DocumentList | null>(null);
  const [kind, setKind] = useState("cv");

  const reload = () => api.get<DocumentList>("/documents").then(setList).catch(() => undefined);
  useEffect(() => {
    void reload();
  }, []);

  const upload = useAction(async (file: File) => {
    const form = new FormData();
    form.set("kind", kind);
    form.set("file", file);
    await api.upload("/documents", form);
    await reload();
    onDone();
  });

  const remove = useAction(async (id: number) => {
    await api.del(`/documents/${id}`);
    await reload();
    onDone();
  });

  const hasCv = list?.documents.some((document) => document.kind === "cv") ?? false;

  return (
    <>
      <Field label="What is this?">
        <select value={kind} onChange={(event) => setKind(event.target.value)}>
          {KINDS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>

      <Field label="File" hint={`We read the text and throw the file away. ${list?.supported.join(", ") ?? ""}`}>
        <input
          type="file"
          disabled={upload.busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload.run(file);
            event.target.value = "";
          }}
        />
      </Field>
      <Error>{upload.error ?? remove.error}</Error>

      {list?.documents.length ? (
        <table>
          <tbody>
            {list.documents.map((document) => (
              <tr key={document.id}>
                <td>{document.filename}</td>
                <td className="muted small">{document.kind}</td>
                <td className="muted small">{document.chars.toLocaleString()} characters</td>
                <td style={{ textAlign: "right" }}>
                  <button className="danger small" onClick={() => void remove.run(document.id)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted small">Nothing uploaded yet.</p>
      )}

      {!hasCv ? <p className="small muted">Your CV is the one document we cannot do without.</p> : null}
    </>
  );
}

export function HistoryStep({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");

  useEffect(() => {
    void api
      .get<{ text: string }>("/onboarding/history")
      .then((stored) => setText(stored.text))
      .catch(() => undefined);
  }, []);

  const save = useAction(async () => {
    await api.put("/onboarding/history", { text });
    onDone();
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <Field
        label="Everything you have done"
        hint="Projects that never made the CV, what you actually did day to day, what you liked and what you would not do again. This is where the parts a CV leaves out come from — the longer the better."
      >
        <textarea value={text} onChange={(event) => setText(event.target.value)} />
      </Field>
      <p className="muted small">{text.length.toLocaleString()} characters — 200 is the minimum.</p>

      <Error>{save.error}</Error>
      <button type="submit" disabled={save.busy || text.length < 200}>
        {save.busy ? "Saving…" : "Save and continue"}
      </button>
    </form>
  );
}
