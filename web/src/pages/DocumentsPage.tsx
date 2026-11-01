/** My documents: your CV and cover letters, the "About you" text, and your name and links.
 *
 * `Files` and `AboutYou` are also onboarding's first screen.
 */
import { useCallback, useEffect, useState } from "react";

import { api, type Schema } from "../api";
import { Card, Error, Field, UploadButton, useAction } from "../ui";
import { IdentityFields } from "./WordsPage";

type DocumentList = Schema<"DocumentList">;

/** Their documents, read once per page and again after every change. */
export function useDocuments() {
  const [list, setList] = useState<DocumentList | null>(null);
  const reload = useCallback(() => api.get<DocumentList>("/documents").then(setList).catch(() => undefined), []);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { list, reload };
}

/** How long what a save found stays on screen. */
const SAID_MS = 8000;

/** What is suggested right now, so a save can say how many words it found. */
async function suggested(): Promise<Set<string>> {
  const body = await api.get<Schema<"WordSuggestions">>("/suggestions");
  return new Set([...body.green, ...body.red].map((each) => each.term));
}

export function DocumentsPage() {
  const { list, reload } = useDocuments();
  const [before, setBefore] = useState<Set<string> | null>(null);
  const [found, setFound] = useState<number | null>(null);

  useEffect(() => {
    void suggested().then(setBefore).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (found === null) return;
    const timer = window.setTimeout(() => setFound(null), SAID_MS);
    return () => window.clearTimeout(timer);
  }, [found]);

  // Every save is read by rules on the spot, so the new suggestions are already there to count.
  const saved = async () => {
    await reload();
    const after = await suggested().catch(() => before ?? new Set<string>());
    setFound([...after].filter((term) => !before?.has(term)).length);
    setBefore(after);
  };

  return (
    <>
      <p className="kicker">My documents</p>
      <h1>Your CV and the rest</h1>
      <p className="lede">We read them to find your words and to make your CVs and cover letters.</p>

      <Card title="Your CV">
        <Files kind="cv" list={list} onChanged={saved} />
      </Card>
      <Card title="Cover letters and other files">
        <Files kind="cover_letter" list={list} onChanged={saved} />
      </Card>
      {/* Untitled: the box's own label says "About you". */}
      <Card>
        <AboutYou onSaved={saved} />
      </Card>
      <Card title="Your name and links">
        <Identity />
      </Card>

      {found !== null ? (
        <p className="toast" role="status">
          {found ? (
            <>
              We found {found} new word{found === 1 ? "" : "s"} for you. <a href="#/words">See them in My search</a>
            </>
          ) : (
            "Saved."
          )}
        </p>
      ) : null}
    </>
  );
}

/** Their CVs, or everything else they uploaded, with a big button to add one. */
export function Files({ kind, list, onChanged }: {
  kind: "cv" | "cover_letter";
  list: DocumentList | null;
  onChanged: () => void;
}) {
  // Kept on screen, not flashed: it says what was left out while they can still act on it.
  const [note, setNote] = useState<string | null>(null);
  const upload = useAction(async (file: File) => {
    const form = new FormData();
    form.set("kind", kind);
    form.set("file", file);
    setNote((await api.upload<Schema<"StoredDocument">>("/documents", form)).note);
    onChanged();
  });
  const remove = useAction(async (id: number) => {
    await api.del(`/documents/${id}`);
    onChanged();
  });

  // The "About you" text is a document too, edited in its own box.
  const shown = (list?.documents ?? []).filter((document) =>
    kind === "cv" ? document.kind === "cv" : !["cv", "history"].includes(document.kind));

  return (
    <>
      {shown.length ? (
        <ul className="files">
          {shown.map((document) => (
            <li key={document.id}>
              <span className="file-name">{document.filename}</span>
              <button type="button" className="secondary small" disabled={remove.busy}
                      onClick={() => {
                        if (window.confirm(`Remove ${document.filename}?`)) void remove.run(document.id);
                      }}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <UploadButton
        label={kind === "cv" ? (shown.length ? "Upload another CV" : "Upload your CV") : "Upload a cover letter"}
        busy={upload.busy}
        accept={list?.supported ?? []}
        // Only the first CV is the thing to do; after it, uploading more is an option.
        quiet={kind !== "cv" || shown.length > 0}
        onFile={(file) => void upload.run(file)}
      />
      {note ? <p className="upload-note">{note}</p> : null}
      <Error>{upload.error ?? remove.error}</Error>
    </>
  );
}

/** The text about themselves they can always edit: what a CV leaves out. */
export function AboutYou({ onSaved }: { onSaved: () => void }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    void api.get<Schema<"HistoryText">>("/onboarding/history").then((body) => setText(body.text));
  }, []);
  const save = useAction(async () => {
    await api.put("/onboarding/history", { text });
    onSaved();
  });

  if (text === null) return null;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <Field label="About you"
             hint="What your CV leaves out: projects, what you did day to day, what you want next. A sentence is enough.">
        <textarea value={text} onChange={(event) => setText(event.target.value)} />
      </Field>
      <Error>{save.error}</Error>
      <button type="submit" className="secondary" disabled={save.busy || !text.trim()}>
        {save.busy ? "Saving…" : "Save"}
      </button>
    </form>
  );
}

function Identity() {
  const [name, setName] = useState<string | null>(null);
  const [links, setLinks] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  useEffect(() => {
    void api.get<Schema<"NameAndLinks">>("/onboarding/identity").then((body) => {
      setName(body.full_name ?? "");
      setLinks(body.links.map((link) => link.url));
    });
  }, []);
  const save = useAction(async () => {
    await api.put("/onboarding/identity", { full_name: name, links: links.map((url) => ({ url })) });
    setDone(true);
  });

  if (name === null) return null;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <IdentityFields name={name} links={links}
                      onName={(value) => { setName(value); setDone(false); }}
                      onLinks={(value) => { setLinks(value); setDone(false); }} />
      <Error>{save.error}</Error>
      <button type="submit" className="secondary" disabled={save.busy || !name.trim()}>
        {save.busy ? "Saving…" : "Save"}
      </button>
      {done ? <p className="saved-flash" role="status">Saved.</p> : null}
    </form>
  );
}
