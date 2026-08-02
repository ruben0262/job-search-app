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
import { Markdown } from "../markdown";
import { Error, useAction } from "../ui";

/** Split a reply into the steps it is made of.
 *
 *  A blank line is what the model already uses to separate one thing it did from the next, so this
 *  reads that rather than inventing a protocol. Fenced code blocks are left whole — a blank line
 *  inside one is part of the code, not a boundary — which is the only case where the naive split
 *  gets it wrong and produces a container holding half a snippet. */
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

/** What setup still needs. Computed server-side from the files themselves, so "done" is a fact
 *  about the database rather than something an agent decided about itself. */
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
  // `cancelling` is the stop requested and the worker not having noticed yet; `cancelled` is a
  // finished state, deliberately not `failed` — nothing went wrong.
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

/** What each file is called, in the user's terms rather than the platform's. A filename like
 *  `02-behavioral-profile.md` tells a non-technical user nothing; "How you work" does. */
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

// The last conversation, kept across mounts so returning to the page renders it instantly while
// the fresh one loads — the split-second "Loading…" was the page throwing away what it knew.
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
  const box = useRef<HTMLTextAreaElement>(null);

  const load = async (id?: string) => {
    const fresh = await api.get<Conversation>(id ? `/setup?conversation_id=${id}` : "/setup");
    cachedConversation = fresh;
    setState(fresh);
  };
  // What setup still needs, answered by the server from the database rather than by the agent's
  // opinion of its own work. It drives the Start button's step and the outstanding list.
  const loadProgress = async () => {
    const body = await api.get<Progress>("/setup/progress").catch(() => null);
    if (body) setProgress(body);
  };

  useEffect(() => {
    // A load that fails silently leaves "Loading…" on screen forever, which reads as a hang
    // rather than an error — the one state a user cannot act on.
    load().catch(() => setFailed(true));
    void loadProgress();
  }, []);

  // Switching chats replaces the transcript wholesale — the cached one belongs to whichever chat
  // was open, and showing it under a different title would be a lie about what was said.
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
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [state?.messages.length]);

  // **The turn runs on a worker, so the page's job is to watch.** While any message is unfinished
  // this re-reads the transcript every second and a half, which is what makes a reply appear as it
  // is written — and what makes leaving and coming back show the same thing rather than nothing.
  // `cancelling` counts as in flight: the worker has not noticed the stop yet, so the turn is
  // still running and the page must keep polling until it settles.
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

  const send = useAction(async (text: string) => {
    // Shown immediately: a message that vanishes for two minutes reads as a dropped message, and
    // the user retypes it. The server writes the same two rows, so the poll replaces these.
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
    // Named explicitly rather than left to "the most recent": a turn that takes two minutes must
    // land in the chat it was typed in, even if something else touched another one meanwhile.
    // This returns as soon as the turn is queued — the reply arrives through the poll below.
    //
    // **A leading `/command` selects the step.** The server accepts the command name separately
    // from the message; without this the page could only ever run the first one, and three of the
    // eight profile files belong to steps nothing could start.
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

  // **Stopping is a request, not a kill.** The worker is usually mid-call and notices between tool
  // steps, so the button reports "stopping" until the row actually settles — claiming otherwise
  // would be contradicted by the file panel a second later.
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
          {/* {state.ready ? (
            <span className="small muted">
              Ready, <a href="#/model">train your model</a> when you like
            </span>
          ) : null} */}
        </div>

        <p className="muted" style={{ maxWidth: "44rem" }}>
          {state.messages.length === 0
            ? "A conversation about your career. Everything you say here is sorted into your profile, the files every CV, cover letter and job match is built from."
            : "Talk to it like you would to a person: new roles, corrections, changes of direction. It knows where things belong. Each chat remembers only itself; your profile is shared across all of them. Training the model that ranks your jobs happens on the Model page, not here."}
        </p>

        {/* The chat switcher. Every chat has its own memory; the profile underneath is shared,
            which is what makes starting a fresh one cheap rather than a loss. */}
        <div className="chat-tabs">
          {state.conversations.map((chat) => (
            <span
              key={chat.id}
              className={`chat-tab${chat.id === state.conversation_id ? " on" : ""}`}
            >
              <button type="button" onClick={() => void openChat.run(chat.id)}>
                {/* The title truncates on its own element. On the button it did not: a flex item
                    with padding kept its full text width and the pill clipped it at the border,
                    losing the first character as well as the last. */}
                <span className="chat-tab-title">{chat.title}</span>
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

        {/* **Success is stated, not inferred.** An agent that has finished and a user who believes
            it has finished are different things, and the gap between them is where "why is my feed
            empty" comes from. This is the same check the trainer refuses on, so the page and the
            trainer cannot disagree. */}
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
              {/* **A button, not a blank box.** Asking a new user to invent the first message is
                  the least helpful screen the product has, and the command they would need to
                  guess is one they have never seen. `next_command` comes from the server, which
                  knows which files are still missing and therefore which step runs next. */}
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
                  {/* Nothing written yet: the turn is queued or the model is still thinking. The
                      dots say working; the sentence says how long, because a turn really can take
                      minutes and an animation alone would imply seconds. */}
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
                      {/* **One container per step, not one per turn.** A reply that alternates a
                          line, a paragraph and a line is several things the agent did, and running
                          them together reads as an undifferentiated wall. The model separates them
                          with blank lines already; this renders that separation. */}
                      {steps(message.content).map((step, position) => (
                        <div key={position} className="turn-step">
                          <span className="turn-step-dot" aria-hidden="true" />
                          <Markdown text={step} />
                        </div>
                      ))}
                      {/* A caret while text is still arriving: the difference between "writing" and
                          "stopped mid-sentence". */}
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
                  {/* **What it did, as a list of steps rather than a sentence.** A turn can write
                      several files and the old single line ran them together with separators, which
                      reads as one event. A rail with a dot per step says how many things happened
                      and lets a reader check the one they care about. */}
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
        {/* **Bottom-left, next to the composer, not up in the transcript.** A long turn's only
            sign of life was a caret somewhere above the fold; a user who had scrolled could not
            tell working from stuck. Stop lives here too, because the two belong together: this is
            the one place that says something is running and the one place to end it. */}
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
          {/* **One control, two jobs.** Stop used to sit up beside the working indicator while
              the send button was greyed out next to it -- two controls, one of them dead, at the
              moment there is exactly one thing a person wants to do. The button a user is already
              looking at becomes the stop. */}
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

      {/* Offered only while the operator has profile files open. The endpoint refuses too, so this
          is about not showing somebody a door that is locked. `state` is repolled, so switching it
          off takes effect on this page without a reload. */}
      {state.files_visible === false ? null : (
      <div style={{ marginTop: "3rem", maxWidth: "48rem" }}>
        <p className="kicker">Your files</p>
        <h2>What we have written down</h2>
        <p className="facts">Click one to read or correct it.</p>
        <div className="files">
          {state.files.map((file) => (
            <button
              key={file.name}
              type="button"
              className={`file${file.filled ? " filled" : ""}`}
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
      )}
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

/* Monochrome inline SVGs, one per file kind — an emoji renders as a colour image on most
   platforms, which is the one thing this palette does not have. */
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
