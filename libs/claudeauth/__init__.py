"""Driving `claude setup-token` on the user's behalf.

The CLI is interactive: it prints an authorization URL, waits on stdin for the code the browser
shows the user, then prints the token. That is three manual steps for a user (run a command, open a
URL, copy a token back), and this module collapses it to one paste of a short code by running the
command here and lending the user only the middle step.

**The awkward part is that the process must stay alive between two HTTP requests.** The URL goes out
in the response to `start`, the user disappears into a browser for a minute or two, and the code
arrives on a later request that must reach *the same* process — the PKCE verifier lives in its
memory and nowhere else. So this keeps a small registry of live sessions keyed by an opaque id.

Three rules follow from that, and they are why this file is not just a `subprocess.run`:

- **Every session is reaped.** A user who closes the tab leaves a process blocked on a read forever.
  Sessions expire on a timer and the process is killed, not merely forgotten.
- **The token never becomes a string that anything else can see.** It is read out of the pty,
  registered with the redaction filter, handed to the caller, and the buffer is cleared. It is not
  logged, not echoed back in an error, and not kept in the registry after `finish`.
- **A pty, not a pipe.** The CLI writes its prompt without a trailing newline and expects a terminal;
  behind a plain pipe it either buffers the URL until exit or refuses to prompt at all.

**Terms note (owner's decision, 2026-08-01, after being shown the policy):** Anthropic's legal and
compliance documentation says OAuth tokens from Free/Pro/Max accounts may not be used in another
product or service, and reserves enforcement without notice against the *user's* account. The
platform keeps this path anyway; `claude_api` remains the alternative and nothing here forces a user
onto OAuth.
"""
from __future__ import annotations

import errno
import logging
import os
import re
import secrets
import signal
import threading
import time
import uuid as _uuid

import pyte

# The URL the CLI prints, and the token it prints at the end. Both are matched rather than parsed:
# the surrounding prose is decoration the CLI is free to change, these two shapes are the contract.
#
# **The host is `claude.com/cai/`, not `claude.ai/`.** Assuming the latter cost a deploy: the regex
# never matched, the read ran to its timeout, and every sign-in returned 503 with no URL to open.
# Matched loosely on the path so a host change does not repeat that.
#
# The class excludes BEL and ESC because the complete URL arrives inside an OSC-8
# hyperlink escape (`ESC ] 8 ; id=… ; <url> BEL`). **Taking the visible text instead would capture a
# truncated URL**, since the CLI word-wraps what it displays but not what it links.
_URL = re.compile(r"https://[^\s\x07\x1b]+/oauth/authorize\?[^\s\x07\x1b]+")
# Deliberately loose on the middle segment. `sk-ant-oat01-` is what 2.1.220 mints, but the
# version and the prefix are Anthropic's to change, and failing to recognise a token we
# have successfully been given is the worst way to lose one.
_TOKEN = re.compile(r"sk-ant-[A-Za-z0-9]{2,12}-[A-Za-z0-9_-]{20,}")

# What the CLI says when Claude turns the code down. Matched alongside the token so a rejection is
# noticed the moment it is printed rather than after the full read timeout: waiting sixty seconds to
# report a mistyped paste reads as the platform being broken.
_REJECTED = re.compile(r"OAuth error:|Invalid code|Request failed"
                       r"|Press\s+Enter\s+to\s+retry")

# Either outcome. `finish` decides which happened by looking at what matched.
_OUTCOME = re.compile(_TOKEN.pattern + "|" + _REJECTED.pattern)

# Cursor moves, colours, and OSC sequences. Stripped before the token is matched: the CLI decorates
# and re-positions as it draws, and an escape landing mid-token would break the match.
_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
                      r"|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")

# How long a user has between "open this link" and pasting the code back. Generous because it spans
# a browser login and possibly a 2FA prompt, bounded because each waiting session is a live process.
SESSION_TTL_SEC = 15 * 60

# Reading the URL should take a second or two; reading the token after the code goes in is a network
# round trip. Neither should be able to wedge a request thread.
URL_WAIT_SEC = 45.0
TOKEN_WAIT_SEC = 60.0

# How long the CLI is given to take the pasted run in before Enter is delivered. Tuned by
# experiment: without a gap the submit is swallowed, and this is short enough that a user does not
# notice it.
PASTE_SETTLE_SEC = 0.8

# A ceiling on live sessions so a scripted caller cannot fork the API host to death. Small on
# purpose: these are humans in a browser, and the reaper frees them as they expire.
MAX_LIVE_SESSIONS = 24

CLI = os.environ.get("CLAUDE_CLI", "claude")

logger = logging.getLogger(__name__)


class OAuthError(RuntimeError):
    """A setup-token run that cannot be completed. The message is shown to the user, so it must
    never carry process output — that output is where the token would be."""


# The most a session's transcript is allowed to grow to. The CLI animates a spinner while it waits,
# so this is mostly discarded redraw; the tail is what any match needs.
MAX_BUFFER = 64 * 1024

# The emulated terminal. Wide enough that nothing the CLI prints wraps, and matching the window size
# set on the pty so the CLI lays out for the screen we are actually rendering.
SCREEN_COLS = 500
SCREEN_ROWS = 100


class _Session:
    """A live `setup-token` process, with something continuously reading its output.

    **The reader thread is the point.** Without it nothing drained the pty between the request that
    took the URL and the request that brought the code back, and the CLI's spinner kept writing the
    whole time the user was in their browser. Once the kernel's pty buffer filled, the CLI blocked
    in `write` and stopped reading its input, so the code we sent was never processed and the read
    that followed waited its full minute for output that could not come. The longer the user took to
    sign in, the more certain the failure -- which is exactly how it behaved.
    """

    __slots__ = ("pid", "fd", "started_at", "user_id", "_buf", "_lock", "_reading",
                 "_screen", "_stream")

    def __init__(self, pid: int, fd: int, user_id: _uuid.UUID) -> None:
        self.pid = pid
        self.fd = fd
        self.started_at = time.monotonic()
        self.user_id = user_id
        self._buf = ""
        # **A real terminal, because deleting the escapes silently corrupts the text.** The CLI
        # renders by cell: it writes `c`, jumps the cursor, writes `de`, and the `o` between them is
        # simply left where an earlier draw already put it. Stripping the escape sequences
        # concatenates what remains, so `code` became `cde` -- and `sk-ant-oat01-…` was stored as
        # `sk-ant-at01-…`, a token that looked plausible, matched the pattern, and was rejected by
        # Anthropic. Only a screen that holds state can reassemble what the user is actually shown.
        self._screen = pyte.Screen(SCREEN_COLS, SCREEN_ROWS)
        self._stream = pyte.Stream(self._screen)
        self._lock = threading.Lock()
        self._reading = True
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        while self._reading:
            try:
                chunk = os.read(self.fd, 4096)
            except OSError as exc:
                # EIO is the normal end of a pty whose child has exited; EAGAIN means nothing yet.
                if exc.errno == errno.EAGAIN:
                    time.sleep(0.05)
                    continue
                break
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            with self._lock:
                # Raw, for the URL: it arrives inside an OSC-8 hyperlink, which is addressed to the
                # terminal rather than drawn on it, so the screen never sees it.
                self._buf = (self._buf + text)[-MAX_BUFFER:]
                self._stream.feed(text)
        self._reading = False

    def text(self, *, strip: bool = False) -> str:
        """The raw stream, or what the terminal is actually displaying.

        `strip` is the rendered screen. The name is kept because every caller means the same thing
        by it -- "the text a person would see" -- which is exactly what deleting escape sequences
        failed to produce.
        """
        with self._lock:
            if not strip:
                return self._buf
            return chr(10).join(line.rstrip() for line in self._screen.display)

    def clear(self) -> None:
        """Forget what has been read so far.

        Called before the code is sent, so a match afterwards is the answer to *this* code and not
        the prompt still sitting in the transcript. Also how the token is dropped once stored.
        """
        with self._lock:
            self._buf = ""
            self._screen.reset()

    def stop(self) -> None:
        self._reading = False


# Process-local. The API runs one worker per container today; if that ever changes this needs to
# become sticky routing or a shared broker, because a session is a *process*, not a row. A second
# worker would hand `finish` to a process that has never heard of the code being pasted.
_SESSIONS: dict[str, _Session] = {}


def _await(session: _Session, pattern: re.Pattern[str], deadline: float, *,
           strip: bool = False) -> str:
    """Wait for `pattern` to appear in what the session has read. Returns the matched text.

    Only the match is returned. The transcript stays on the session, where `clear` can wipe it,
    rather than being handed to a caller that might put it in an error message -- after `finish`
    it contains the token.
    """
    while time.monotonic() < deadline:
        found = pattern.search(session.text(strip=strip))
        if found:
            return found.group(0)
        if not session._reading:
            # The process ended without printing what we were waiting for. Nothing more is coming,
            # so say so now instead of holding the request until the deadline.
            break
        time.sleep(0.1)
    raise OAuthError("the Claude sign-in did not respond in time, please try again")


def _kill(session: _Session) -> None:
    """End a session's process and release its descriptor. Safe to call twice."""
    session.stop()
    try:
        os.kill(session.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.waitpid(session.pid, os.WNOHANG)
    except ChildProcessError:
        pass
    try:
        os.close(session.fd)
    except OSError:
        pass


def reap(now: float | None = None) -> int:
    """Kill every session past its TTL. Called on each start, so no timer thread is needed."""
    clock = time.monotonic() if now is None else now
    expired = [key for key, s in _SESSIONS.items() if clock - s.started_at > SESSION_TTL_SEC]
    for key in expired:
        _kill(_SESSIONS.pop(key))
    return len(expired)


def start(user_id: _uuid.UUID) -> tuple[str, str]:
    """Spawn `claude setup-token` and return (session_id, authorization_url).

    One session per user at a time: starting again abandons the previous attempt, which is what a
    user who reloaded the modal means, and stops a retry loop stacking up processes.
    """
    import pty  # noqa: PLC0415 - POSIX only, and this module is imported on Windows for its tests

    reap()
    for key, existing in list(_SESSIONS.items()):
        if existing.user_id == user_id:
            _kill(_SESSIONS.pop(key))
    if len(_SESSIONS) >= MAX_LIVE_SESSIONS:
        raise OAuthError("too many sign-ins in progress, please try again in a few minutes")

    try:
        pid, fd = pty.fork()
    except OSError as exc:
        raise OAuthError("could not start the Claude sign-in") from exc

    if pid == 0:  # pragma: no cover - the child never returns
        # No inherited credential may leak into the child: a token in the environment would make the
        # CLI skip the browser flow entirely and mint nothing.
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        env["TERM"] = "dumb"
        env["COLUMNS"] = "500"
        env["NO_COLOR"] = "1"
        try:
            os.execvpe(CLI, [CLI, "setup-token"], env)
        finally:
            os._exit(127)

    os.set_blocking(fd, False)
    # **A wide terminal, so the CLI never wraps.** At the default 80 columns it breaks the token
    # across lines mid-string, and no amount of escape-stripping reassembles that reliably.
    try:
        import fcntl  # noqa: PLC0415
        import struct  # noqa: PLC0415
        import termios  # noqa: PLC0415

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 100, 500, 0, 0))
    except (ImportError, OSError):  # pragma: no cover - best effort, stripping still helps
        pass
    session = _Session(pid, fd, user_id)
    try:
        url = _await(session, _URL, time.monotonic() + URL_WAIT_SEC)
    except OAuthError:
        _kill(session)
        raise

    session_id = secrets.token_urlsafe(24)
    _SESSIONS[session_id] = session
    return session_id, url


def finish(session_id: str, user_id: _uuid.UUID, code: str) -> str:
    """Feed the browser's code to the waiting process and return the token it prints.

    **A rejected code keeps the session alive.** The CLI does not exit on a bad exchange; it prints
    `OAuth error: …` and offers `Press Enter to retry`, and the link the user already has in their
    browser stays valid. Killing the session here forced them back to a fresh URL, and the fresh URL
    carried a new PKCE challenge — so the code sitting in the tab they had open could then never
    work. That turned one mistyped paste into a loop nobody could escape.

    Only a session that actually produced a token, or one the user abandoned, is consumed.
    """
    session = _SESSIONS.get(session_id)
    if session is None:
        raise OAuthError("that sign-in expired, please start again")
    if session.user_id != user_id:
        # Cannot happen through the routes, which look the session up for the caller. Checked
        # anyway: handing one user's token to another is the worst bug this file could have.
        _SESSIONS.pop(session_id, None)
        _kill(session)
        raise OAuthError("that sign-in expired, please start again")

    # Everything printed before this belongs to the prompt, not to the answer.
    session.clear()
    try:
        # **Bracketed paste, and Enter as a separate write.** The obvious `code + "\r"` in one write
        # silently failed: the CLI is a TUI reading keystrokes, and a single burst outran it. Eight
        # of ninety-two characters were dropped and the trailing carriage return was swallowed by
        # its paste debounce, so the code sat half-entered on the prompt and nothing was ever
        # submitted. It looked exactly like a hang, because it was one -- the CLI was still waiting
        # for input we thought we had sent.
        #
        # Wrapping in `ESC [ 200 ~ … ESC [ 201 ~` tells it a paste is arriving, so it takes the run
        # whole; the pause then lets it settle before Enter is delivered as a keypress of its own.
        os.write(session.fd, b"\x1b[200~" + code.strip().encode() + b"\x1b[201~")
        time.sleep(PASTE_SETTLE_SEC)
        os.write(session.fd, b"\r")
        token = _await(session, _OUTCOME, time.monotonic() + TOKEN_WAIT_SEC, strip=True)
    except OSError as exc:
        _SESSIONS.pop(session_id, None)
        _kill(session)
        raise OAuthError("the Claude sign-in closed before it finished") from exc
    except OAuthError:
        # **Say what the CLI actually printed.** This failure has now been diagnosed wrongly twice
        # from the outside, because the only evidence was a user-facing sentence that deliberately
        # carries no process output. So the transcript is logged here instead, with anything
        # token-shaped replaced first -- the log is the one place it is safe to describe, and only
        # because it never contains the credential.
        transcript = _TOKEN.sub("<token>", session.text(strip=True))[-1200:]
        logger.warning("setup-token produced no recognised outcome; tail=%r", transcript)
        _SESSIONS.pop(session_id, None)
        _kill(session)
        raise OAuthError("the Claude sign-in did not answer, please start again") from None

    if not token.startswith("sk-ant-"):
        # Claude rejected it. Clear the prompt so the same session can take another attempt, and
        # say which code to use: the usual cause is a code copied from a tab opened by an earlier
        # attempt, which belongs to a challenge that no longer exists.
        try:
            os.write(session.fd, b"\r")
        except OSError:
            pass
        raise OAuthError("Claude did not accept that code. Copy it again from the tab you just "
                         "authorized in, and paste the whole thing.")

    # The transcript holds the token now, so it goes before anything else can read it.
    session.clear()
    _SESSIONS.pop(session_id, None)
    _kill(session)
    return token


def cancel(session_id: str) -> None:
    """Drop a session the user backed out of, rather than leaving it to the TTL."""
    session = _SESSIONS.pop(session_id, None)
    if session is not None:
        _kill(session)
