/** Build your profile — a conversation with the setup agents. */
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { Markdown } from "../markdown";
import { Error, useAction } from "../ui";

/** Split a reply into the steps it is made of. */
function steps(text: string): string[] {
  const blocks: string[] = [];
  let current: string[] = [];
  let fenced = false;

  for (const line of text.split("\n")) {
    if (line.trimStart().startsWith("```")) fenced = !fenced;
    if (!fenced && line.trim() === "") {
      if (current.length) blocks.push(current.join("\n"));
      current = [];
      continue;
    }
    current.push(line);
  }
  if (current.length) blocks.push(current.join("\n"));
  return blocks.length ? blocks : [text];
}

/** What setup still needs. */
type Progress = {
  complete: boolean;
  missing: string[];
  required: string[];
  next_command: string | null;
  outstanding_by_command: Record<string, string[]>;
  commands: string[];
  may_train: boolean;
};

type Message = {
  id?: number;
  role: "user" | "assistant";
  content: string;
  // `cancelling` is the stop requested and the worker not having noticed yet.
  status: "pending" | "streaming" | "done" | "failed" | "cancelling" | "cancelled";
  error: string | null;
  files_written: string[];
  at: string | null;
};

type ProfileFile = {
  name: string;
  kind: string;
  filled: boolean;
  updated_at: string | null;
};

type ChatSummary = {
  id: string;
  title: string;
  created_at: string | null;
  last_message_at: string | null;
};

type Conversation = {
  conversation_id: string;
  conversations: ChatSummary[];
  max_conversations: number;
  messages: Message[];
  files: ProfileFile[];
  /** Operator-controlled. Absent on older responses, so only an explicit false hides them. */
  files_visible?: boolean;
  ready: boolean;
};

/** What each file is called, in the user's terms rather than the platform's. */
const NAMES: Record<string, string> = {
  "context.md": "Who you are",
  "01-candidate-profile.md": "Your history",
  "02-behavioral-profile.md": "How you work",
  "04-job-evaluation.md": "What fits you",
  "05-cv-templates.md": "CV shapes",
  "07-interview-prep.md": "Interview prep",
  "08-role-angles.md": "Role angles",
  "label.md": "Job scoring",
  "base_cv.html": "Your base CV",
  "tailor_cv.md": "CV instructions",
  "tailor_cover.md": "Cover letter instructions",
  "score.md": "Job assessment",
  "interview.md": "Interview instructions",
  "outreach.md": "Outreach instructions",
  "build_cvs.md": "CV checks",
};

const DESCRIPTIONS: Record<string, string> = {
  "context.md": "Who you are, the summary every document is written from",
  "01-candidate-profile.md": "Your full history: roles, education, projects",
  "02-behavioral-profile.md": "How you answer questions about how you work",
  "04-job-evaluation.md": "What makes a job a good fit for you",
  "05-cv-templates.md": "How your CV should be shaped per role",
  "07-interview-prep.md": "Your examples, ready for an interview",
  "08-role-angles.md": "The kinds of job you go for, and what to lead with for each",
  "label.md": "How your model scores jobs",
  "base_cv.html": "Your CV, before it is tailored to a job",
  "tailor_cv.md": "The instructions for writing your tailored CVs",
  "tailor_cover.md": "The instructions for writing your cover letters",
  "score.md": "How a job is assessed against your profile",
  "interview.md": "How your interview prep is written",
  "outreach.md": "How your messages to hiring managers are written",
  "build_cvs.md": "The checks a finished CV has to pass",
};

/** Grow with the text, shrink back after a send — the composer every chat app taught. */
function autogrow(box: HTMLTextAreaElement): void {
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, 200)}px`;
}

// The last conversation, kept across mounts so returning to the page renders it instantly while the fresh.
let cachedConversation: Conversation | null = null;

/** Forgotten on sign-out, or the next account flashes this one's conversation. */
export function clearConversationCache(): void {
  cachedConversation = null;
}

export function SetupPage({ onChanged }: { onChanged: () => void }) {
  const [state, setState] = useState<Conversation | null>(cachedConversation);
  const [progress, setProgress] = useState<Progress | null>(null);
  const commands = progress?.commands ?? [];
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState("");
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [fileText, setFileText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  // Whether the view should follow new output. True until the reader scrolls away.
  const [pinned, setPinned] = useState(true);
  const box = useRef<HTMLTextAreaElement>(null);

  const [renaming, setRenaming] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  /** Save the typed name, or quietly drop the edit if it is unchanged or empty. */
  const commitRename = async (id: string) => {
    const wanted = draftTitle.trim();
    setRenaming(null);
    const current = state?.conversations.find((chat) => chat.id === id)?.title;
    if (!wanted || wanted === current) return;
    await api.patch(`/setup/conversations/${id}`, { title: wanted });
    await load(state?.conversation_id);
  };

  const load = async (id?: string) => {
    const fresh = await api.get<Conversation>(id ? `/setup?conversation_id=${id}` : "/setup");
    cachedConversation = fresh;
    setState(fresh);
  };
  // What setup still needs, answered by the server from the database rather than by the agent.
  const loadProgress = async () => {
    const body = await api.get<Progress>("/setup/progress").catch(() => null);
    if (body) setProgress(body);
  };

  useEffect(() => {
    // A load that fails silently leaves "Loading…" on screen forever.
    load().catch(() => setFailed(true));
    void loadProgress();
  }, []);

  // Switching chats replaces the transcript wholesale — the cached one belongs to whichever chat was open.
  const openChat = useAction(async (id: string) => {
    await load(id);
  });

  const newChat = useAction(async () => {
    const created = await api.post<{ id: string }>("/setup/conversations");
    await load(created.id);
    box.current?.focus();
  });

  const deleteChat = useAction(async (id: string) => {
    await api.del(`/setup/conversations/${id}`);
    await load();
  });
  /** Follow the reply as it is written. */
  const messages = state?.messages;
  const tail = messages?.length
    ? `${messages.length}:${messages[messages.length - 1]!.content.length}`
    : "";

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const onScroll = () => {
      // A margin rather than an exact match: smooth scrolling lands a pixel or two short.
      const gap = element.scrollHeight - element.scrollTop - element.clientHeight;
      setPinned(gap < 120);
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => element.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (pinned) bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [tail, pinned]);

  // The turn runs on a worker, so the page's job is to watch.
  const inFlight = state?.messages.some((message) =>
    ["pending", "streaming", "cancelling"].includes(message.status),
  );
  const stopping = state?.messages.some((message) => message.status === "cancelling");
  const openConversation = state?.conversation_id;
  useEffect(() => {
    if (!inFlight || !openConversation) return;
    const timer = window.setInterval(() => {
      load(openConversation).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [inFlight, openConversation]);

  /** Re-read progress the moment a turn stops running. */
  const wasInFlight = useRef(false);
  useEffect(() => {
    if (wasInFlight.current && !inFlight) {
      void loadProgress();
      onChanged();
    }
    wasInFlight.current = Boolean(inFlight);
  }, [inFlight]);

  const send = useAction(async (text: string) => {
    // Shown immediately: a message that vanishes for two minutes reads as a dropped message.
    setState((previous) =>
      previous
        ? {
            ...previous,
            messages: [
              ...previous.messages,
              { role: "user", content: text, status: "done", error: null, files_written: [], at: null },
              { role: "assistant", content: "", status: "pending", error: null, files_written: [], at: null },
            ],
          }
        : previous,
    );
    setDraft("");
    if (box.current) {
      box.current.style.height = "auto";
    }
    // Named explicitly rather than left to "the most recent".
    const named = /^\/([a-z-]+)\s*/.exec(text)?.[1];
    const agent = named && commands.includes(named) ? named : undefined;
    await api.post("/setup/message", {
      message: text,
      conversation_id: state?.conversation_id,
      ...(agent ? { agent } : {}),
    });
    await load(state?.conversation_id);
    await loadProgress();
    onChanged();
  });

  // Stopping is a request, not a kill.
  const stop = useAction(async () => {
    const running = state?.messages.find(
      (message) => message.role === "assistant" && ["pending", "streaming"].includes(message.status),
    );
    if (!running?.id) return;
    await api.post(`/setup/message/${running.id}/stop`).catch(() => undefined);
    await load(state?.conversation_id);
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

  if (failed) return <p className="error">This page did not load. Refresh to try again.</p>;
  if (!state) return <p className="muted">Loading…</p>;

  if (openFile) {
    return (
      <div className="overview">
        <h1>{NAMES[openFile] ?? openFile}</h1>
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
          Editing by hand is fine for a quick correction. For anything larger, a new role, a change
          of direction, tell the assistant instead: it knows where things belong.
        </p>
      </div>
    );
  }

  return (
    <>
      <div style={{ maxWidth: "48rem" }}>
        <p className="kicker">Your profile</p>
        <div className="spread">
          <h1>Build your profile</h1>
          {/* {state.ready ? */}
        </div>

        <p className="muted" style={{ maxWidth: "44rem" }}>
          {state.messages.length === 0
            ? "A conversation about your career. Everything you say here is sorted into your profile, the files every CV, cover letter and job match is built from."
            : "Talk to it like you would to a person: new roles, corrections, changes of direction. It knows where things belong. Each chat remembers only itself; your profile is shared across all of them. Training the model that ranks your jobs happens on the Model page, not here."}
        </p>

        {/* The chat switcher. */}
        <div className="chat-tabs">
          {state.conversations.map((chat) => (
            <span
              key={chat.id}
              className={`chat-tab${chat.id === state.conversation_id ? " on" : ""}`}
            >
              {renaming === chat.id ? (
                /* Editing in place, in the tab itself. */
                <input
                  className="chat-tab-rename"
                  value={draftTitle}
                  autoFocus
                  maxLength={80}
                  onChange={(event) => setDraftTitle(event.target.value)}
                  onBlur={() => void commitRename(chat.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void commitRename(chat.id);
                    if (event.key === "Escape") setRenaming(null);
                  }}
                />
              ) : (
                <button type="button" onClick={() => void openChat.run(chat.id)}>
                  {/* The title truncates on its own element. */}
                  <span className="chat-tab-title">{chat.title}</span>
                </button>
              )}
              <button
                type="button"
                className="chat-tab-x"
                aria-label={`Rename ${chat.title}`}
                title="Rename this chat"
                onClick={() => {
                  setDraftTitle(chat.title);
                  setRenaming(chat.id);
                }}
              >
                <PencilIcon />
              </button>
              <button
                type="button"
                className="chat-tab-x"
                aria-label={`Delete ${chat.title}`}
                title="Delete this chat"
                disabled={deleteChat.busy}
                onClick={() => {
                  if (window.confirm(`Delete "${chat.title}"? What was said is gone. Your profile is not affected.`))
                    void deleteChat.run(chat.id);
                }}
              >
                ×
              </button>
            </span>
          ))}
          {state.conversations.length < state.max_conversations ? (
            <button
              type="button"
              className="chat-tab-new"
              disabled={newChat.busy}
              onClick={() => void newChat.run()}
            >
              + New chat
            </button>
          ) : (
            <span className="chat-tab-full">
              {state.max_conversations} chats max · delete one to start another
            </span>
          )}
        </div>
        <Error>{openChat.error ?? newChat.error ?? deleteChat.error}</Error>

        {/* Success is stated, not inferred. */}
        {progress ? (
          progress.complete ? (
            <p className="small" style={{ color: "var(--score)" }}>
              Setup complete, all {progress.required.length} profile files are written. Your model
              can be trained from the Model page.
            </p>
          ) : null
        ) : null}

        <div className="chat" ref={scroller}>
          {state.messages.length === 0 ? (
            <div className="chat-empty">
              {/* A button, not a blank box. */}
              <p className="md-heading">Let's build your profile</p>
              <p className="muted">
                This is a conversation about your career. It asks the questions, and sorts your
                answers into the files every CV, cover letter and job match is built from.
              </p>
              <p style={{ marginTop: "1.25rem" }}>
                <button
                  disabled={send.busy || inFlight}
                  onClick={() => void send.run(`/${progress?.next_command ?? "setup"}`)}
                >
                  {send.busy ? "Starting…" : "Start setup"}
                </button>
              </p>
              <p className="hint">
                Takes a few passes. You can stop at any point and pick up where you left off.
              </p>
            </div>
          ) : null}
          {state.messages.map((message, index) => (
            <div key={index} className={`turn ${message.role}`}>
              {message.role === "assistant" ? (
                <div className={`turn-body${message.status === "failed" ? " turn-failed" : ""}`}>
                  {/* Nothing written yet: the turn is queued or the model is still thinking. */}
                  {message.status === "pending" && !message.content ? (
                    <span className="pending">
                      <span className="typing" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                      Thinking. This can take a couple of minutes, you can leave this page and come
                      back, the answer will be here.
                    </span>
                  ) : (
                    <>
                      {/* One container per step, not one per turn. */}
                      {steps(message.content).map((step, position) => (
                        <div key={position} className="turn-step">
                          <span className="turn-step-dot" aria-hidden="true" />
                          <Markdown text={step} />
                        </div>
                      ))}
                      {/* A caret while text is still arriving. */}
                      {message.status === "streaming" ? <span className="caret" /> : null}
                    </>
                  )}
                  {message.status === "cancelled" ? (
                    <span className="turn-stopped">
                      You stopped this. Anything it had already written is saved.
                    </span>
                  ) : null}
                  {message.status === "failed" && message.error ? (
                    <span className="turn-error">{message.error}</span>
                  ) : null}
                  {/* What it did, as a list of steps rather than a sentence. */}
                  {message.files_written.length > 0 ? (
                    <ul className="activity">
                      {message.files_written.map((name) => (
                        <li key={name}>
                          <span className="activity-dot" aria-hidden="true" />
                          <span>
                            Updated <strong>{NAMES[name] ?? name}</strong>
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : (
                <div className="turn-bubble">{message.content}</div>
              )}
            </div>
          ))}
          <div ref={bottom} />
        </div>

        <Error>{send.error}</Error>
        {/* Bottom-left, next to the composer, not up in the transcript. */}
        {inFlight ? (
          <div className="working">
            <span className="typing" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <span className="muted small">
              {stopping ? "Stopping, it will finish the step it is on." : "Working…"}
            </span>
          </div>
        ) : null}
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            if (draft.trim() && !send.busy && !inFlight) void send.run(draft.trim());
          }}
        >
          <textarea
            ref={box}
            rows={1}
            value={draft}
            placeholder={
              inFlight
                ? "Waiting for the reply…"
                : state.messages.length === 0
                  ? "Say hello to begin"
                  : "Message the assistant"
            }
            disabled={send.busy || inFlight}
            onChange={(event) => {
              setDraft(event.target.value);
              autogrow(event.target);
            }}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter breaks the line — the grammar every chat app taught.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (draft.trim() && !send.busy && !inFlight) void send.run(draft.trim());
              }
            }}
          />
          {/* One control, two jobs. */}
          {inFlight ? (
            <button
              type="button"
              className="composer-send stop"
              aria-label="Stop"
              title="Stop"
              disabled={stop.busy || stopping}
              onClick={() => void stop.run()}
            >
              <StopIcon />
            </button>
          ) : (
            <button
              type="submit"
              className="composer-send"
              aria-label="Send"
              disabled={send.busy || !draft.trim()}
            >
              <SendIcon />
            </button>
          )}
        </form>
      </div>

      <div style={{ marginTop: "3rem", maxWidth: "48rem" }}>
        <p className="kicker">Your files</p>
        <h2>What we have written down</h2>
        {/* Listed either way, openable only when the operator has said so. */}
        <p className="facts">
          {state.files_visible === false
            ? "These are being reviewed, so they cannot be opened yet."
            : "Click one to read or correct it."}
        </p>
        <div className="files">
          {state.files.map((file) => (
            <button
              key={file.name}
              type="button"
              className={`file${file.filled ? " filled" : ""}`}
              disabled={state.files_visible === false}
              title={state.files_visible === false ? "Not open for reading yet" : undefined}
              onClick={() => void openEditor(file.name)}
            >
              <FileIcon kind={file.kind} />
              <span>
                <span className="file-name">{NAMES[file.name] ?? file.name}</span>
                <span className="file-state">{file.filled ? "written" : "not yet"}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

/** A square. Deliberately not a cross: stopping ends the turn, it does not undo or close. */
function StopIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" />
    </svg>
  );
}

/** A pencil, for renaming. */
function PencilIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 20h4L19 9l-4-4L4 16v4z"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 19V5M5.5 11.5L12 5l6.5 6.5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* Monochrome inline SVGs, one per file kind — an emoji renders as a colour image on most platforms. */
function FileIcon({ kind }: { kind: string }) {
  const stroke = { stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" } as const;
  if (kind === "context")
    // A person: the file that says who this is.
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="8" r="3.6" {...stroke} />
        <path d="M4.5 20c1.3-3.4 4.1-5 7.5-5s6.2 1.6 7.5 5" {...stroke} />
      </svg>
    );
  if (kind === "prompt")
    // A pen: instructions for writing on the user's behalf.
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M4 20l1.2-4.2L16.4 4.6a2 2 0 012.9 0l.1.1a2 2 0 010 2.9L8.2 18.8 4 20z" {...stroke} strokeLinejoin="round" />
      </svg>
    );
  if (kind === "template")
    // A layout: the shape a document is poured into.
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="4" y="4" width="16" height="16" rx="2" {...stroke} />
        <path d="M4 9.5h16M9.5 9.5V20" {...stroke} />
      </svg>
    );
  // Default, including "skill": a document with lines.
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6 3.5h8.5L19 8v12.5H6z" {...stroke} strokeLinejoin="round" />
      <path d="M9 12h6M9 15.5h6" {...stroke} />
    </svg>
  );
}
