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
      <Card title="CVs and cover letters we made for you">
        <Made />
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

/** The documents made for jobs and kept: how full the storage is, each one to download or delete, and
 * a way to clear them all when it is full. */
function Made() {
  const [kept, setKept] = useState<Schema<"SavedDocuments"> | null>(null);
  const [sure, setSure] = useState(false);
  useEffect(() => {
    void api.get<Schema<"SavedDocuments">>("/saved").then(setKept).catch(() => undefined);
  }, []);
  const remove = useAction(async (id: number) => setKept(await api.del<Schema<"SavedDocuments">>(`/saved/${id}`)));
  const clear = useAction(async () => {
    setKept(await api.del<Schema<"SavedDocuments">>("/saved"));
    setSure(false);
  });

  if (!kept) return <p className="muted small">Loading…</p>;
  const megabytes = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  const share = kept.allowance_bytes ? Math.min(kept.used_bytes / kept.allowance_bytes, 1) : 0;
  const full = kept.allowance_bytes !== null && share >= 0.95;
  return (
    <>
      <p className="small muted">
        Each one you make is kept here to download again, the newest for each job.
      </p>
      {kept.allowance_bytes !== null ? (
        <div className="storage" role="meter" aria-label="Storage used" aria-valuemin={0}
             aria-valuemax={kept.allowance_bytes} aria-valuenow={kept.used_bytes}>
          <div className="storage-bar"><span style={{ width: `${share * 100}%` }} className={full ? "full" : ""} /></div>
          <span className="small">
            {megabytes(kept.used_bytes)} of {megabytes(kept.allowance_bytes)} used
            {full ? " — full: new ones are not kept until you delete some" : ""}
          </span>
        </div>
      ) : null}
      {kept.documents.length === 0 ? (
        <p className="muted">None yet. Tap “Make my CV” under a job, and it is kept here.</p>
      ) : (
        <ul className="made-list">
          {kept.documents.map((each) => (
            <li key={each.id}>
              <span>
                <strong>{each.kind === "cv" ? "CV" : "Cover letter"}</strong>{" "}
                for {each.job_title ?? "a job no longer listed"}
                {each.company ? ` at ${each.company}` : ""}
                <span className="small muted"> · {Math.round(each.size / 1024)} KB</span>
              </span>
              <span className="made-actions">
                <a className="button small" href={each.download_url} download>Download</a>
                <button type="button" className="secondary small" disabled={remove.busy}
                        onClick={() => void remove.run(each.id)}>
                  Delete
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {kept.documents.length ? (
        sure ? (
          <p className="row">
            <span className="small">Delete all {kept.documents.length}?</span>
            <button type="button" className="danger small" disabled={clear.busy} onClick={() => void clear.run()}>
              Yes, delete them all
            </button>
            <button type="button" className="secondary small" onClick={() => setSure(false)}>Keep them</button>
          </p>
        ) : (
          <button type="button" className="secondary small" onClick={() => setSure(true)}>Clear all</button>
        )
      ) : null}
      <Error>{remove.error ?? clear.error}</Error>
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
