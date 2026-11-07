/** My documents: your CV and cover letters, the "About you" text, and your name and links.
 *
 * `Files` and `AboutYou` are also onboarding's first screen.
 */
import { useEffect, useState } from "react";

import { api, type Schema } from "../api";
import { useKept } from "../kept";
import { ago, useNow } from "../time";
import { Card, Error, Field, Prompt, UploadButton, useAction } from "../ui";
import { IdentityFields } from "./WordsPage";

type DocumentList = Schema<"DocumentList">;

/** What this page reads, as `kept.ts` keeps it: shown at once on every visit. */
export const DOCUMENTS = "/documents";
export const SAVED = "/saved";
export const HISTORY = "/onboarding/history";
export const IDENTITY = "/onboarding/identity";

/** Their documents, as last read, read again on every visit and after every change. */
export function useDocuments() {
  const { data: list, reload } = useKept<DocumentList>(DOCUMENTS);
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

/** "Made": how old a kept document may be and still be shown, in days. */
const MADE: [string, number | null][] = [["Made any time", null], ["Made over a week ago", 7],
                                         ["Made over a month ago", 30], ["Made over 3 months ago", 90]];

/** The documents made for jobs and kept: how full the storage is, and the documents themselves, folded
 * until opened (owner, 2026-11-02). A search and an age pick some out, to download or to delete: the
 * old ones go, the newest stay. */
function Made() {
  const { data: kept, set } = useKept<Schema<"SavedDocuments">>(SAVED);
  const [query, setQuery] = useState("");
  const [older, setOlder] = useState<number | null>(null);
  const [sure, setSure] = useState(false);
  const remove = useAction(async (id: number) => {
    const left = await api.del<Schema<"SavedDocuments">>(`/saved/${id}`);
    set(() => left);
  });
  const removeShown = useAction(async (ids: number[]) => {
    const params = new URLSearchParams(ids.map((id) => ["ids", `${id}`]));
    const left = await api.del<Schema<"SavedDocuments">>(`/saved?${params}`);
    set(() => left);
    setSure(false);
  });

  if (!kept) return <p className="muted small">Loading…</p>;
  const megabytes = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  const share = kept.allowance_bytes ? Math.min(kept.used_bytes / kept.allowance_bytes, 1) : 0;
  const full = kept.allowance_bytes !== null && share >= 0.95;
  const words = query.trim().toLowerCase();
  const before = older === null ? null : Date.now() - older * 86_400_000;
  const shown = kept.documents.filter((each) =>
    (!words || `${each.job_title ?? ""} ${each.company ?? ""}`.toLowerCase().includes(words))
    && (before === null || new Date(each.created_at).getTime() < before));
  const many = shown.length === 1 ? "this one"
    : shown.length === kept.documents.length ? `all ${shown.length}` : `these ${shown.length}`;
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
        <details className="fold-plain made">
          <summary>
            Show your {kept.documents.length} {kept.documents.length === 1 ? "document" : "documents"}
          </summary>
          <div className="toolbar">
            <input type="search" placeholder="Search by job or company" aria-label="Search your documents"
                   value={query} onChange={(event) => setQuery(event.target.value)} />
            <select aria-label="Made" value={older ?? ""}
                    onChange={(event) => setOlder(event.target.value ? Number(event.target.value) : null)}>
              {MADE.map(([label, days]) => <option key={label} value={days ?? ""}>{label}</option>)}
            </select>
          </div>
          {shown.length === 0 ? <p className="muted small">None of yours match.</p> : (
            <ul className="made-list">
              {shown.map((each) => (
                <li key={each.id}>
                  <span>
                    <strong>{each.kind === "cv" ? "CV" : "Cover letter"}</strong>{" "}
                    for {each.job_title ?? "a job no longer listed"}
                    {each.company ? ` at ${each.company}` : ""}
                    <span className="small muted">
                      {" "}· Made {new Date(each.created_at).toLocaleDateString(undefined,
                        { day: "numeric", month: "short", year: "numeric" })} · {Math.round(each.size / 1024)} KB
                    </span>
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
          {shown.length ? (
            <button type="button" className="secondary small" onClick={() => setSure(true)}>
              Delete {many}
            </button>
          ) : null}
        </details>
      )}
      {sure ? (
        <Prompt title={`Delete ${many}?`} onClose={() => setSure(false)}>
          <p className="small muted">Gone for good. You can make any of them again from its job.</p>
          <Error>{removeShown.error}</Error>
          <div className="row">
            <button type="button" className="danger" disabled={removeShown.busy}
                    onClick={() => void removeShown.run(shown.map((each) => each.id))}>
              {removeShown.busy ? "Deleting…" : "Delete"}
            </button>
            <button type="button" className="secondary" onClick={() => setSure(false)}>Keep</button>
          </div>
        </Prompt>
      ) : null}
      <Error>{remove.error}</Error>
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
  const now = useNow();
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
  const use = useAction(async (id: number, inUse: boolean) => {
    await api.patch(`/documents/${id}`, { in_use: inUse });
    onChanged();
  });

  // The "About you" text is a document too, edited in its own box.
  const shown = (list?.documents ?? []).filter((document) =>
    kind === "cv" ? document.kind === "cv" : !["cv", "history"].includes(document.kind));

  // What is up, as solid cards that say so, apart from the dashed place to add more: someone looking
  // fast must not take one for the other (owner, 2026-11-02).
  return (
    <>
      {shown.length ? (
        <p className="small muted">We use every file that is switched on. Switch one off to keep it without using it.</p>
      ) : null}
      {shown.length ? (
        <ul className="files" aria-label="Uploaded">
          {shown.map((document) => (
            <li key={document.id} className={document.in_use ? "file" : "file off"}>
              <svg className="file-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h6" />
              </svg>
              <span className="file-text">
                <span className="file-name">{document.filename}</span>
                <span className="small muted">
                  {[document.uploaded_at ? `Uploaded ${ago(document.uploaded_at, now)}` : null,
                    `${document.chars.toLocaleString()} characters read`].filter(Boolean).join(" · ")}
                </span>
              </span>
              <label className="file-use">
                <input type="checkbox" role="switch" aria-label={`Use ${document.filename}`}
                       checked={document.in_use} disabled={use.busy}
                       onChange={(event) => void use.run(document.id, event.target.checked)} />
                <span className="file-status">{document.in_use ? "In use" : "Not used"}</span>
              </label>
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
      {shown.length ? <p className="files-add">{kind === "cv" ? "Add another CV" : "Add another file"}</p> : null}
      <UploadButton
        label={kind === "cv" ? (shown.length ? "Upload another CV" : "Upload your CV") : "Upload a cover letter"}
        busy={upload.busy}
        accept={list?.supported ?? []}
        // Only the first CV is the thing to do; after it, uploading more is an option.
        quiet={kind !== "cv" || shown.length > 0}
        onFile={(file) => void upload.run(file)}
      />
      {note ? <p className="upload-note">{note}</p> : null}
      <Error>{upload.error ?? remove.error ?? use.error}</Error>
    </>
  );
}

/** The text about themselves they can always edit: what a CV leaves out. */
export function AboutYou({ onSaved }: { onSaved: () => void }) {
  const { data, reload } = useKept<Schema<"HistoryText">>(HISTORY);
  // What they typed, until it is saved: a fresh read never writes over it.
  const [typed, setText] = useState<string | null>(null);
  const text = typed ?? data?.text ?? null;
  const save = useAction(async () => {
    await api.put(HISTORY, { text });
    await reload();
    setText(null);
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
  const { data, reload } = useKept<Schema<"NameAndLinks">>(IDENTITY);
  // What they typed, until it is saved: a fresh read never writes over it.
  const [typed, setTyped] = useState<{ name: string; links: string[] } | null>(null);
  const [done, setDone] = useState(false);
  const name = typed?.name ?? data?.full_name ?? "";
  const links = typed?.links ?? data?.links.map((link) => link.url) ?? [];
  const setName = (value: string) => setTyped({ name: value, links });
  const setLinks = (value: string[]) => setTyped({ name, links: value });
  const save = useAction(async () => {
    await api.put(IDENTITY, { full_name: name, links: links.map((url) => ({ url })) });
    await reload();
    setTyped(null);
    setDone(true);
  });

  if (!data) return null;
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
