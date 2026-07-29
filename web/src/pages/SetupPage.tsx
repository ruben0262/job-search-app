/**
 * Build your profile — a conversation with the setup agents.
 *
 * **The user is answering questions about themselves.** That the answers become eight markdown
 * files is the platform's business, not theirs: nothing here asks them to approve a filename,
 * read a diff, or understand what a scaffold is. The file list is on the right for anyone curious
 * enough to look, and it is the only place the machinery shows.
 *
 * This replaces "process your documents", which spent one call on a schema and produced a profile
 * with no career in it (`REBUILD_PLAN.md`).
 *
 * **A turn can take minutes.** Opus 5 reads a document set and writes several files in one go, so
 * the pending state has to be honest rather than optimistic — a spinner that implies seconds is
 * worse than a line that says what is happening.
 */
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { Error, useAction } from "../ui";

type Message = {
  role: "user" | "assistant";
  content: string;
  files_written: string[];
  at: string | null;
};

type ProfileFile = {
  name: string;
  kind: string;
  filled: boolean;
  updated_at: string | null;
};

type Conversation = {
  messages: Message[];
  files: ProfileFile[];
  ready: boolean;
};

/** What each file is, in the user's terms rather than the platform's. They never need this, but
 *  somebody who opens the panel deserves better than a filename. */
const DESCRIPTIONS: Record<string, string> = {
  "context.md": "Who you are — the summary every document is written from",
  "01-candidate-profile.md": "Your full history: roles, education, projects",
  "02-behavioral-profile.md": "How you answer questions about how you work",
  "04-job-evaluation.md": "What makes a job a good fit for you",
  "05-cv-templates.md": "How your CV should be shaped per role",
  "07-interview-prep.md": "Your examples, ready for an interview",
  "label.md": "How your model scores jobs",
  "base_cv.html": "Your CV, before it is tailored to a job",
};

export function SetupPage({ onChanged }: { onChanged: () => void }) {
  const [state, setState] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState("");
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [fileText, setFileText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const load = async () => setState(await api.get<Conversation>("/setup"));
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [state?.messages.length]);

  const send = useAction(async (text: string) => {
    // Shown immediately: a message that vanishes into a pending state for two minutes reads as a
    // dropped message, and the user retypes it.
    setState((previous) =>
      previous
        ? { ...previous, messages: [...previous.messages, { role: "user", content: text, files_written: [], at: null }] }
        : previous,
    );
    setDraft("");
    await api.post("/setup/message", { message: text });
    await load();
    onChanged();
  });

  const start = useAction(async () => {
    await api.post("/setup/start");
    onChanged();
  });

  const saveFile = useAction(async (name: string, content: string) => {
    await api.put(`/setup/files/${name}`, { content });
    setOpenFile(null);
    await load();
  });

  const openEditor = async (name: string) => {
    const file = await api.get<{ content: string }>(`/setup/files/${name}`);
    setFileText(file.content);
    setOpenFile(name);
  };

  if (!state) return <p className="muted">Loading…</p>;

  if (openFile) {
    return (
      <div className="overview">
        <h1>{openFile}</h1>
        <p className="lede">{DESCRIPTIONS[openFile] ?? "One of your profile files."}</p>
        <textarea
          value={fileText}
          onChange={(event) => setFileText(event.target.value)}
          style={{ minHeight: "26rem", fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}
        />
        <Error>{saveFile.error}</Error>
        <div className="row" style={{ marginTop: "1rem" }}>
          <button disabled={saveFile.busy} onClick={() => void saveFile.run(openFile, fileText)}>
            {saveFile.busy ? "Saving…" : "Save"}
          </button>
          <button className="secondary" onClick={() => setOpenFile(null)}>
            Cancel
          </button>
        </div>
        <p className="facts" style={{ marginTop: "1rem" }}>
          Editing by hand is fine for a quick correction. For anything larger — a new role, a change
          of direction — tell the assistant instead: it knows where things belong.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="overview">
        <h1>Build your profile</h1>
        <p className="lede">
          {state.messages.length === 0
            ? "A conversation about your career. What comes out of it is what every CV, cover letter and job match is built from."
            : state.ready
              ? "Your profile is ready. Keep going if there is more to add."
              : "Keep going — nothing is lost if you close this and come back."}
        </p>

        <div className="chat">
          {state.messages.map((message, index) => (
            <div key={index} className={`bubble ${message.role}`}>
              {message.content}
              {message.files_written.length > 0 ? (
                <span className="saved">Saved: {message.files_written.join(", ")}</span>
              ) : null}
            </div>
          ))}
          {send.busy ? (
            <div className="bubble assistant pending">
              Reading and writing this down. This can take a couple of minutes.
            </div>
          ) : null}
          <div ref={bottom} />
        </div>

        <Error>{send.error}</Error>
        <div className="row" style={{ marginTop: "1rem" }}>
          <input
            value={draft}
            placeholder={state.messages.length === 0 ? "Say hello to begin" : "Your answer"}
            disabled={send.busy}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && draft.trim() && !send.busy) void send.run(draft.trim());
            }}
          />
          <button disabled={send.busy || !draft.trim()} onClick={() => void send.run(draft.trim())}>
            Send
          </button>
        </div>

        {state.ready ? (
          <div style={{ marginTop: "2rem" }}>
            <h2>Next: score your first jobs</h2>
            <p className="facts">
              This reads the jobs already collected and scores them against your profile. It runs on
              your own Claude key and takes a few minutes.
            </p>
            <Error>{start.error}</Error>
            <button disabled={start.busy} onClick={() => void start.run()}>
              {start.busy ? "Starting…" : "Start scoring"}
            </button>
          </div>
        ) : null}
      </div>

      <div className="overview" style={{ marginTop: "2.5rem" }}>
        <h2>What we have written down</h2>
        <dl className="stats" style={{ marginTop: "0.75rem" }}>
          {state.files.map((file) => (
            <div key={file.name} className="stat">
              <dt style={{ fontSize: "0.9375rem" }}>
                <a href="#/setup" onClick={() => void openEditor(file.name)}>
                  {DESCRIPTIONS[file.name] ?? file.name}
                </a>
              </dt>
              <dd>{file.filled ? "written" : "not yet"}</dd>
            </div>
          ))}
        </dl>
      </div>
    </>
  );
}
