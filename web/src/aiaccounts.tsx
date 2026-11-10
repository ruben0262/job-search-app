/** Your AI accounts, in Settings (owner, 2026-11-09): what writes your CVs and cover letters. Sign in to
 * your own Claude, ChatGPT or Google Gemini account, or add an API key. Link as many as you like; one is
 * in use, and switching one on switches the others off. Nothing of a key or a sign-in is ever shown back:
 * each is its name and when it was linked.
 *
 * Said in the order someone new needs it (owner, 2026-11-10: "so people understand what this section
 * is"): what it is for, the ones linked, and the three ways to link one, each by its provider's mark. */
import { useEffect, useId, useRef, useState } from "react";

import { ApiError, api, type Schema } from "./api";
import { useKept } from "./kept";
import { Error, Field, Prompt, useAction } from "./ui";

type Accounts = Schema<"AiAccounts">;
type Account = Schema<"AiAccount">;
type LoginKind = "claude_login" | "codex_login" | "gemini_login";
type KeyKind = "claude_api" | "openai_api" | "gemini_api";

/** What this card reads, as `kept.ts` keeps it. */
export const AI_ACCOUNTS = "/ai-accounts";

const SIGN_IN: Record<LoginKind, { name: string; who: string; page: string }> = {
  claude_login: { name: "Claude", who: "With a Claude Pro or Max plan", page: "Claude" },
  codex_login: { name: "ChatGPT", who: "With your ChatGPT plan", page: "OpenAI" },
  gemini_login: { name: "Google Gemini", who: "With your Google account", page: "Google" },
};

/** The three providers, and which each account kind is. */
type Provider = "anthropic" | "openai" | "google";
const PROVIDER: Record<Account["kind"], Provider> = {
  claude_login: "anthropic", claude_api: "anthropic", codex_login: "openai", openai_api: "openai",
  gemini_login: "google", gemini_api: "google",
};

/** Each provider's own mark, so the one they use is found at a glance: Claude's in its orange, OpenAI's
 * in the text colour (its guidelines ask for black or white), Gemini's in Google's gradient. The shapes
 * are Simple Icons' (Claude, OpenAI) and Google's own (gstatic, Gemini's sparkle). On each sign-in and on
 * each account linked (owner, 2026-11-10). */
function Mark({ kind }: { kind: Account["kind"] }) {
  // Its own gradient on each, as there may be several on the page; React's ids, made safe for `url(#…)`.
  const gradient = `gemini-${useId().replace(/[^\w-]/g, "")}`;
  const provider = PROVIDER[kind];
  return (
    <span className="ai-mark" aria-hidden="true">
      {provider === "anthropic" ? (
        <svg viewBox="0 0 24 24"><path fill="#D97757" d="m4.7144 15.9555 4.7174-2.6471.079-.2307-.079-.1275h-.2307l-.7893-.0486-2.6956-.0729-2.3375-.0971-2.2646-.1214-.5707-.1215-.5343-.7042.0546-.3522.4797-.3218.686.0608 1.5179.1032 2.2767.1578 1.6514.0972 2.4468.255h.3886l.0546-.1579-.1336-.0971-.1032-.0972L6.973 9.8356l-2.55-1.6879-1.3356-.9714-.7225-.4918-.3643-.4614-.1578-1.0078.6557-.7225.8803.0607.2246.0607.8925.686 1.9064 1.4754 2.4893 1.8336.3643.3035.1457-.1032.0182-.0728-.164-.2733-1.3539-2.4467-1.445-2.4893-.6435-1.032-.17-.6194c-.0607-.255-.1032-.4674-.1032-.7285L6.287.1335 6.6997 0l.9957.1336.419.3642.6192 1.4147 1.0018 2.2282 1.5543 3.0296.4553.8985.2429.8318.091.255h.1579v-.1457l.1275-1.706.2368-2.0947.2307-2.6957.0789-.7589.3764-.9107.7468-.4918.5828.2793.4797.686-.0668.4433-.2853 1.8517-.5586 2.9021-.3643 1.9429h.2125l.2429-.2429.9835-1.3053 1.6514-2.0643.7286-.8196.85-.9046.5464-.4311h1.0321l.759 1.1293-.34 1.1657-1.0625 1.3478-.8804 1.1414-1.2628 1.7-.7893 1.36.0729.1093.1882-.0183 2.8535-.607 1.5421-.2794 1.8396-.3157.8318.3886.091.3946-.3278.8075-1.967.4857-2.3072.4614-3.4364.8136-.0425.0304.0486.0607 1.5482.1457.6618.0364h1.621l3.0175.2247.7892.522.4736.6376-.079.4857-1.2142.6193-1.6393-.3886-3.825-.9107-1.3113-.3279h-.1822v.1093l1.0929 1.0686 2.0035 1.8092 2.5075 2.3314.1275.5768-.3218.4554-.34-.0486-2.2039-1.6575-.85-.7468-1.9246-1.621h-.1275v.17l.4432.6496 2.3436 3.5214.1214 1.0807-.17.3521-.6071.2125-.6679-.1214-1.3721-1.9246L14.38 17.959l-1.1414-1.9428-.1397.079-.674 7.2552-.3156.3703-.7286.2793-.6071-.4614-.3218-.7468.3218-1.4753.3886-1.9246.3157-1.53.2853-1.9004.17-.6314-.0121-.0425-.1397.0182-1.4328 1.9672-2.1796 2.9446-1.7243 1.8456-.4128.164-.7164-.3704.0667-.6618.4008-.5889 2.386-3.0357 1.4389-1.882.929-1.0868-.0062-.1579h-.0546l-6.3385 4.1164-1.1293.1457-.4857-.4554.0608-.7467.2307-.2429 1.9064-1.3114Z" /></svg>
      ) : provider === "openai" ? (
        <svg viewBox="0 0 24 24"><path fill="currentColor" d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z" /></svg>
      ) : (
        <svg viewBox="0 0 28 28">
          <defs>
            <radialGradient id={gradient} cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse"
                            gradientTransform="translate(2.77876 11.3795) rotate(18.6832) scale(29.8025 238.737)">
              <stop offset="0.0671246" stopColor="#9168C0" />
              <stop offset="0.342551" stopColor="#5684D1" />
              <stop offset="0.672076" stopColor="#1BA1E3" />
            </radialGradient>
          </defs>
          <path fill={`url(#${gradient})`} d="M14 28C14 26.0633 13.6267 24.2433 12.88 22.54C12.1567 20.8367 11.165 19.355 9.905 18.095C8.645 16.835 7.16333 15.8433 5.46 15.12C3.75667 14.3733 1.93667 14 0 14C1.93667 14 3.75667 13.6383 5.46 12.915C7.16333 12.1683 8.645 11.165 9.905 9.905C11.165 8.645 12.1567 7.16333 12.88 5.46C13.6267 3.75667 14 1.93667 14 0C14 1.93667 14.3617 3.75667 15.085 5.46C15.8317 7.16333 16.835 8.645 18.095 9.905C19.355 11.165 20.8367 12.1683 22.54 12.915C24.2433 13.6383 26.0633 14 28 14C26.0633 14 24.2433 14.3733 22.54 15.12C20.8367 15.8433 19.355 16.835 18.095 18.095C16.835 19.355 15.8317 20.8367 15.085 22.54C14.3617 24.2433 14 26.0633 14 28Z" />
        </svg>
      )}
    </span>
  );
}

const KEYS: Record<KeyKind, [string, string, string]> = {
  claude_api: ["Claude API key", "From console.anthropic.com, with credit on it.", "sk-ant-…"],
  openai_api: ["OpenAI API key", "From platform.openai.com.", "sk-…"],
  gemini_api: ["Gemini API key", "From aistudio.google.com, on a project with billing turned on.", "AIza…"],
};

/** The least a switch takes to say it has switched, so it is seen to move (owner, 2026-11-10: "we
 * cannot properly see if they switch at all"). The knob's own glide is in `styles.css`. */
const SWITCH_MS = 900;

const linked = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const wait = (ms: number) => new Promise((done) => window.setTimeout(done, ms));

/** A tab opened in the click itself, or Safari blocks it; the sign-in sends it to the provider's page. */
function openTab(): Window | null {
  const tab = window.open("", "_blank");
  if (!tab) return null;
  tab.opener = null;
  tab.document.title = "Opening the sign-in…";
  tab.document.body.textContent = "Opening the sign-in page…";
  return tab;
}

export function AiAccounts() {
  const { data, set } = useKept<Accounts>(AI_ACCOUNTS);
  const [signing, setSigning] = useState<{ kind: LoginKind; tab: Window | null } | null>(null);
  const [adding, setAdding] = useState(false);
  // The one switch on its way, shown moving before the list says it has moved.
  const [moving, setMoving] = useState<{ id: string; on: boolean } | null>(null);
  // Every change answers with the list as it now is.
  const changed = (next: Accounts) => set(() => next);
  const switchTo = useAction(async (account: Account, on: boolean) => {
    setMoving({ id: account.id, on });
    try {
      const [next] = await Promise.all([api.put<Accounts>(`${AI_ACCOUNTS}/${account.id}`, { in_use: on }),
                                        wait(SWITCH_MS)]);
      changed(next);
    } finally {
      setMoving(null);
    }
  });
  const remove = useAction(async (account: Account) => {
    changed(await api.del<Accounts>(`${AI_ACCOUNTS}/${account.id}`));
  });

  if (!data) return <p className="muted small">Loading…</p>;
  return (
    <>
      <p className="ai-lead">
        Your CVs and cover letters are written by an AI you already have. Link it once, then press{" "}
        <strong>Make my CV</strong> under any job.
      </p>

      {data.accounts.length ? (
        <>
          <ul className="ai-accounts" aria-label="Linked AI accounts">
            {data.accounts.map((account) => {
              const going = moving?.id === account.id ? moving : null;
              return (
                <li key={account.id} className={account.in_use ? "in-use" : undefined}>
                  <Mark kind={account.kind} />
                  <span className="ai-account-name">
                    <strong>{account.label}</strong>
                    <span className="small muted">
                      {account.kind.endsWith("_api") ? "API key" : "Signed in"} · linked{" "}
                      {linked.format(new Date(account.linked_at))}
                    </span>
                    {/* The one model its provider writes with, picked by us, not by them (owner, 2026-11-10). */}
                    <span className="small muted">
                      Writes with {account.model ? <code>{account.model}</code> : "the model your plan includes"}
                    </span>
                  </span>
                  <label className="ai-switch">
                    {/* Not disabled while one moves, which would grey every switch out: a click then is
                        simply not taken. */}
                    <input type="checkbox" role="switch" aria-label={`Use ${account.label}`}
                           checked={going ? going.on : account.in_use}
                           onChange={(event) => {
                             if (!moving) void switchTo.run(account, event.target.checked);
                           }} />
                    <span className={going ? "ai-switch-word moving" : "ai-switch-word"}>
                      {going ? (going.on ? "Turning on…" : "Turning off…") : account.in_use ? "In use" : "Off"}
                    </span>
                  </label>
                  <button type="button" className="linklike small" disabled={remove.busy}
                          onClick={() => {
                            if (window.confirm(`Remove ${account.label}?`)) void remove.run(account);
                          }}>
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
          {data.accounts.length > 1 ? (
            <p className="small muted">One is used at a time. Switching one on switches the other off.</p>
          ) : null}
        </>
      ) : null}
      <Error>{switchTo.error ?? remove.error}</Error>

      <h3 className="ai-heading">{data.accounts.length ? "Link another" : "Link yours"}</h3>
      <div className="ai-providers">
        {(Object.keys(SIGN_IN) as LoginKind[]).map((kind) => (
          <button key={kind} type="button" className="secondary ai-provider"
                  aria-label={`Sign in with ${SIGN_IN[kind].name}`}
                  onClick={() => setSigning({ kind, tab: openTab() })}>
            <Mark kind={kind} />
            <span className="ai-provider-text">
              <strong>{SIGN_IN[kind].name}</strong>
              <span className="small muted">{SIGN_IN[kind].who}</span>
              <span className="ai-provider-go" aria-hidden="true">Sign in →</span>
            </span>
          </button>
        ))}
      </div>
      <p className="small muted">
        You sign in on their own page, so we never see your password. What it gives us is stored encrypted
        and never shown again, not even to you. Remove it here any time.
      </p>

      <details className="fold-plain" open={adding} onToggle={(event) => setAdding(event.currentTarget.open)}>
        <summary>Use an API key instead</summary>
        <p className="small muted">For developers who pay Anthropic, OpenAI or Google per use.</p>
        {adding ? <AddKey onAdded={(next) => {
          setAdding(false);
          changed(next);
        }} /> : null}
      </details>

      {signing ? <SignIn kind={signing.kind} tab={signing.tab} onClose={() => setSigning(null)} onLinked={(next) => {
        setSigning(null);
        changed(next);
      }} /> : null}
    </>
  );
}

/** An API key: checked with its provider before it is kept, and in use from then on. */
function AddKey({ onAdded }: { onAdded: (next: Accounts) => void }) {
  const [kind, setKind] = useState<KeyKind>("claude_api");
  const [value, setValue] = useState("");
  const save = useAction(async (anyway: boolean) => {
    onAdded(await api.post<Accounts>(`${AI_ACCOUNTS}/keys`, { kind, value: value.trim(), allow_unvalidated: anyway }));
    setValue("");
  });
  const [label, hint, placeholder] = KEYS[kind];
  return (
    <form onSubmit={(event) => {
      event.preventDefault();
      void save.run(false);
    }}>
      <Field label="Which key">
        <select value={kind} onChange={(event) => setKind(event.target.value as KeyKind)}>
          {(Object.keys(KEYS) as KeyKind[]).map((each) => <option key={each} value={each}>{KEYS[each][0]}</option>)}
        </select>
      </Field>
      <Field label={label} hint={hint}>
        <input type="password" value={value} placeholder={placeholder} autoComplete="off"
               onChange={(event) => setValue(event.target.value)} />
      </Field>
      <Error>{save.error}</Error>
      <div className="row">
        <button type="submit" disabled={save.busy || value.trim().length < 8}>
          {save.busy ? "Checking it…" : "Add this key"}
        </button>
        {/* An outage at the provider must not stop someone linking a key they know is right. */}
        {save.error ? (
          <button type="button" className="secondary small" disabled={save.busy} onClick={() => void save.run(true)}>
            Add it anyway, I know it is right
          </button>
        ) : null}
      </div>
    </form>
  );
}

/** "ended": the provider turned the code down, or the sign-in ran out; only a new link carries on. */
type Phase = "starting" | "waiting" | "finishing" | "ended" | "done";

/** One sign-in, from opening the provider's page to the account being linked. Claude and Google show a
 * code to paste back here; ChatGPT shows one here, to enter on OpenAI's page, and this waits for it. */
function SignIn({ kind, tab, onClose, onLinked }: {
  kind: LoginKind;
  /** The tab opened in the click, which the sign-in page goes into. */
  tab: Window | null;
  onClose: () => void;
  onLinked: (next: Accounts) => void;
}) {
  const [phase, setPhase] = useState<Phase>("starting");
  const [started, setStarted] = useState<Schema<"SignInStarted"> | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const opened = useRef(tab);
  const { name, page } = SIGN_IN[kind];
  const pasted = kind !== "codex_login";

  const begin = async () => {
    setError(null);
    setCode("");
    setStarted(null);
    setPhase("starting");
    try {
      const body = await api.post<Schema<"SignInStarted">>(`${AI_ACCOUNTS}/signin/${kind}`, {});
      setStarted(body);
      setPhase("waiting");
      if (opened.current && !opened.current.closed) opened.current.location.replace(body.url);
    } catch (caught) {
      opened.current?.close();
      setPhase("ended");
      setError(caught instanceof ApiError ? caught.detail : "could not start the sign-in");
    }
  };

  // Once, when the sheet opens.
  useEffect(() => {
    void begin();
  }, []);

  // A refusal says whether the same link still takes a code (400) or the sign-in has ended (410).
  const refused = (caught: unknown) => {
    const gone = !(caught instanceof ApiError) || caught.status === 410;
    setPhase(gone ? "ended" : "waiting");
    setError(caught instanceof ApiError ? caught.detail : "the sign-in stopped");
  };

  const finish = async (typed: string | null) => {
    if (!started) return null;
    const result = await api.post<Schema<"SignInResult">>(`${AI_ACCOUNTS}/signin/${kind}/finish`,
                                                           { session_id: started.session_id, code: typed });
    return result.status === "done" ? result.accounts ?? null : null;
  };

  const done = (next: Accounts) => {
    setPhase("done");
    // Held a moment, so the confirmation is read rather than flashed.
    window.setTimeout(() => onLinked(next), 1200);
  };

  // ChatGPT: ask until OpenAI's page has been given the code. Each ask waits up to twenty seconds there.
  useEffect(() => {
    if (pasted || phase !== "waiting" || !started) return;
    let live = true;
    const ask = async () => {
      while (live) {
        try {
          const next = await finish(null);
          if (next) {
            if (live) done(next);
            return;
          }
        } catch (caught) {
          if (live) refused(caught);
          return;
        }
      }
    };
    void ask();
    return () => {
      live = false;
    };
  }, [pasted, phase, started]);

  const submit = async () => {
    if (!code.trim()) return;
    setError(null);
    setPhase("finishing");
    try {
      const next = await finish(code.trim());
      if (next) done(next);
      else setPhase("waiting");
    } catch (caught) {
      refused(caught);
    }
  };

  const again = () => {
    opened.current = openTab();
    void begin();
  };

  const close = () => {
    if (started && phase !== "done" && phase !== "ended") {
      void api.post(`${AI_ACCOUNTS}/signin/cancel`, { session_id: started.session_id });
    }
    onClose();
  };

  const title = phase === "starting" ? `Opening ${name}…` : phase === "done" ? `${name} is linked`
    : phase === "ended" ? "That did not work" : `Sign in to ${name}`;
  return (
    <Prompt dismissable={false} onClose={close} title={title}>
      {phase === "starting" ? (
        <p className="small muted">Getting your sign-in page ready. This takes a few seconds.</p>
      ) : phase === "done" ? (
        <p className="ai-linked">It is in use now, and the Make buttons are under every job.</p>
      ) : phase === "ended" ? null : (
        <ol className="ai-steps">
          <li>
            {pasted ? `Approve on the ${page} page that opened.` : `Sign in on the ${page} page that opened.`}
            {started ? (
              <span className="small muted"> Not there? <a href={started.url} target="_blank"
                                                           rel="noopener noreferrer">Open it here</a>.</span>
            ) : null}
          </li>
          {pasted ? (
            <>
              <li>{page} then shows you a code. Copy all of it.</li>
              <li>
                <Field label="Paste the code here">
                  <input value={code} autoFocus autoComplete="off" spellCheck={false}
                         disabled={phase === "finishing"}
                         onChange={(event) => setCode(event.target.value)}
                         onKeyDown={(event) => {
                           if (event.key === "Enter") void submit();
                         }} />
                </Field>
              </li>
            </>
          ) : (
            <>
              <li>
                Type in this code when it asks:
                <span className="row">
                  <span className="device-code" aria-label="The code to enter">{started?.code}</span>
                  <button type="button" className="secondary small" onClick={() => {
                    if (started?.code) void navigator.clipboard?.writeText(started.code).then(() => setCopied(true));
                  }}>
                    {copied ? "Copied" : "Copy"}
                  </button>
                </span>
              </li>
              <li className="muted">This finishes by itself once you have. Waiting for {page}…</li>
            </>
          )}
        </ol>
      )}
      {error ? <p className="error">{error}</p> : null}
      {phase !== "done" ? (
        <div className="row">
          <button type="button" className="secondary" onClick={close}>Cancel</button>
          {phase === "ended" ? (
            <button type="button" onClick={again}>Get a new link</button>
          ) : pasted ? (
            <button type="button" disabled={phase !== "waiting" || !code.trim()} onClick={() => void submit()}>
              {phase === "finishing" ? "Checking…" : "Link it"}
            </button>
          ) : null}
        </div>
      ) : null}
    </Prompt>
  );
}
