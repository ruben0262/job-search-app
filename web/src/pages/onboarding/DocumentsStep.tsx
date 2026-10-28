/** Their documents: the CV and the rest, and the "about you" text they can edit. */
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

/** What an upload answers with. `note` is set when the file was longer than we keep. */
type Uploaded = { note: string | null };

export function DocumentsStep({ onDone }: { onDone: () => void }) {
  const [list, setList] = useState<DocumentList | null>(null);
  const [kind, setKind] = useState("cv");
  // Kept on screen after the upload, not flashed: the user has to be able to act on it.
  const [note, setNote] = useState<string | null>(null);

  const reload = () => api.get<DocumentList>("/documents").then(setList).catch(() => undefined);
  useEffect(() => {
    void reload();
  }, []);

  const upload = useAction(async (file: File) => {
    const form = new FormData();
    form.set("kind", kind);
    form.set("file", file);
    const result = await api.upload<Uploaded>("/documents", form);
    setNote(result.note);
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
      {/* Not an error: the file was kept. It says what was left out, while they can still act. */}
      {note ? <p className="upload-note">{note}</p> : null}

      {/* The history is its own page with its own single file. */}
      {list?.documents.filter((document) => document.kind !== "history").length ? (
        <table>
          <tbody>
            {list.documents.filter((document) => document.kind !== "history").map((document) => (
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
  const [uploaded, setUploaded] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

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

  // Either is accepted, and both end up in the same place.
  const upload = useAction(async (file: File) => {
    if (
      text.trim().length > 0 &&
      !window.confirm(
        "This replaces your current history, what is written there now disappears. Replace it?",
      )
    ) {
      return;
    }
    const form = new FormData();
    form.set("kind", "history");
    form.set("file", file);
    const result = await api.upload<Uploaded>("/documents", form);
    setNote(result.note);
    const stored = await api.get<{ text: string }>("/onboarding/history");
    setText(stored.text);
    setUploaded(file.name);
    onDone();
  });

  const ready = text.trim().length > 0;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) void save.run();
      }}
    >
      <Field
        label="Write it here"
        hint="Projects that never made the CV, what you actually did day to day, what you would not do again. This is where the parts a CV leaves out come from."
      >
        <textarea value={text} autoFocus onChange={(event) => setText(event.target.value)} />
      </Field>
      <p className="small muted">{text.length.toLocaleString()} characters</p>

      <div className="either">or</div>

      <Field label="Upload a document" hint="PDF, Word, or plain text. We read it and show you what we found.">
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
      {uploaded ? <p className="small muted">Read from {uploaded}. Edit it above if you like.</p> : null}
      {note ? <p className="upload-note">{note}</p> : null}

      <Error>{save.error ?? upload.error}</Error>
      <button type="submit" className={ready ? "advance ready" : "advance"} disabled={save.busy}>
        {save.busy ? "Saving…" : "Save"}
      </button>
    </form>
  );
}
