"""Signing a person in to their own AI account, through that provider's own command-line tool (owner,
2026-11-09): Claude Code's `claude auth login`, Codex's `codex login --device-auth`, Gemini CLI's Google
sign-in. Run by the sign-in broker (`services/oauthbroker`), the one process that holds these sessions.

Each sign-in runs in a terminal of its own (a pty, read through a real terminal screen) and a folder of
its own. The person opens the link it prints; Claude and Google then show a code they paste back here,
and Codex shows a code here that they enter on OpenAI's page. A code turned down ends the sign-in: Claude
Code exits when it refuses one, so the person starts again for a new link. Signed in is the tool writing
its login files (`libs.llm.cli.TOOLS`): those are taken as the account's secret, for the API to seal,
and the folder is deleted. Unlike `claude setup-token`, which this replaces, Claude's full sign-in
renews itself.
"""
from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import secrets
import shutil
import signal
import tempfile
import threading
import time
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path

import pyte

from libs.llm import cli

logger = logging.getLogger(__name__)

# The link each tool prints, taken whole: it can arrive inside an OSC-8 hyperlink the screen never shows.
_URLS = {"claude_login": re.compile(r"https://[^\s\x07\x1b]+/oauth/authorize\?[^\s\x07\x1b]+"),
         "codex_login": re.compile(r"https://auth\.openai\.com/[^\s\x07\x1b]+"),
         "gemini_login": re.compile(r"https://accounts\.google\.com/o/oauth2/[^\s\x07\x1b]+")}
# The one-time code Codex shows, which the person types in on OpenAI's page.
_DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b")
# What a tool says when the provider turns a pasted code down.
_REJECTED = re.compile(r"OAuth error:|Invalid code|Request failed|Press\s+Enter\s+to\s+retry|Login failed"
                       r"|invalid_grant|Failed to (?:authenticate|exchange)|Error authenticating")
# The tools that take a code pasted back. Each reads it as a plain line, so it is typed in as text: the
# markers of a bracketed paste arrive as part of the code, and Claude refused every code sent that way.
_PASTED = ("claude_login", "gemini_login")

# How long a person has between "open this link" and finishing.
SESSION_TTL_SEC = 15 * 60
# Printing the link takes a second or two; the login after a code is a network round trip.
URL_WAIT_SEC = 45.0
DONE_WAIT_SEC = 60.0
# How long one look at an unfinished Codex sign-in waits before saying "not yet": the page asks again.
POLL_WAIT_SEC = 20.0
# How long a tool is given to take a pasted code in before Enter is delivered, and to write the file
# naming the account after the login itself.
PASTE_SETTLE_SEC = 0.8
SETTLE_SEC = 1.0
# A ceiling on live sessions, so a scripted caller cannot fork the broker to death.
MAX_LIVE_SESSIONS = 24
# The most of a session's transcript that is kept, and the size of its screen.
MAX_BUFFER = 64 * 1024
SCREEN_COLS, SCREEN_ROWS = 500, 100


class SignInError(RuntimeError):
    """A sign-in that cannot be completed, said so the person can act on it."""


@dataclass(frozen=True)
class Started:
    session_id: str
    url: str
    # Codex's one-time code, which the person enters on OpenAI's page; None where they paste one back.
    code: str | None


@dataclass(frozen=True)
class SignedIn:
    # The login files, as `libs.llm.cli` keeps them: the account's secret, for the API to seal.
    secret: str
    label: str


class _Session:
    """A live sign-in: its tool's process, the screen it draws, and the folder it writes into."""

    def __init__(self, pid: int, fd: int, user_id: _uuid.UUID, kind: str, home: Path) -> None:
        self.pid, self.fd, self.user_id, self.kind, self.home = pid, fd, user_id, kind, home
        self.started_at = time.monotonic()
        self._buf = ""
        # A real terminal, because deleting the escapes silently corrupts the text.
        self._screen = pyte.Screen(SCREEN_COLS, SCREEN_ROWS)
        self._stream = pyte.Stream(self._screen)
        self._lock = threading.Lock()
        self.reading = True
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        while self.reading:
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
                self._buf = (self._buf + text)[-MAX_BUFFER:]
                self._stream.feed(text)
        self.reading = False

    def text(self, *, screen: bool = False) -> str:
        """The raw stream, or what the terminal is displaying."""
        with self._lock:
            return chr(10).join(line.rstrip() for line in self._screen.display) if screen else self._buf


# Process-local: the broker runs one worker, so every session is in this dict.
_SESSIONS: dict[str, _Session] = {}


def start(user_id: _uuid.UUID, kind: str) -> Started:
    """Start a sign-in and return the link to open (and Codex's code to enter)."""
    import pty  # noqa: PLC0415 - POSIX only

    if kind not in cli.TOOLS:
        raise SignInError(f"there is no sign-in for {kind!r}")
    reap()
    for key, existing in list(_SESSIONS.items()):
        if existing.user_id == user_id:  # one at a time: starting again abandons the last attempt
            _end(_SESSIONS.pop(key))
    if len(_SESSIONS) >= MAX_LIVE_SESSIONS:
        raise SignInError("too many sign-ins in progress, please try again in a few minutes")

    home = Path(tempfile.mkdtemp(prefix="signin-"))
    home.chmod(0o700)
    cli.prepare(kind, home)
    tool = cli.TOOLS[kind]
    argv = [tool.path(), *tool.signin]
    env = cli.environment(kind, home)
    env["COLUMNS"] = str(SCREEN_COLS)
    try:
        pid, fd = pty.fork()
    except OSError as exc:
        shutil.rmtree(home, ignore_errors=True)
        raise SignInError(f"could not start the {tool.name} sign-in") from exc
    if pid == 0:  # pragma: no cover - the child never returns
        try:
            os.chdir(home)
            # No shell: the argument list is fixed here, and nothing a person types reaches it.
            os.execvpe(argv[0], argv, env)  # noqa: S606
        finally:
            os._exit(127)

    os.set_blocking(fd, False)
    with contextlib.suppress(ImportError, OSError):  # a wide terminal, so the link never wraps
        import fcntl  # noqa: PLC0415
        import struct  # noqa: PLC0415
        import termios  # noqa: PLC0415

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", SCREEN_ROWS, SCREEN_COLS, 0, 0))
    session = _Session(pid, fd, user_id, kind, home)
    try:
        url = _await(session, _URLS[kind], URL_WAIT_SEC)
        code = _await(session, _DEVICE_CODE, URL_WAIT_SEC, screen=True) if kind == "codex_login" else None
    except SignInError:
        _end(session)
        raise
    session_id = secrets.token_urlsafe(24)
    _SESSIONS[session_id] = session
    return Started(session_id, url, code)


def finish(session_id: str, user_id: _uuid.UUID, code: str | None) -> SignedIn | None:
    """Complete a sign-in: the pasted code for Claude and Google, nothing for Codex. Returns the
    account, or None while Codex is still waiting for the person on OpenAI's page."""
    session = _SESSIONS.get(session_id)
    if session is None or session.user_id != user_id:
        raise SignInError("that sign-in expired, please start again")
    name = cli.TOOLS[session.kind].name
    pasted = session.kind in _PASTED
    if pasted:
        if not code or not re.fullmatch(r"[\w#.~/+=-]{4,512}", code.strip()):
            raise SignInError(f"That code does not look right. Copy it again from {name}'s page.")
        try:
            os.write(session.fd, code.strip().encode())
            time.sleep(PASTE_SETTLE_SEC)
            os.write(session.fd, b"\r")
        except OSError as exc:
            _drop(session_id)
            raise SignInError(f"the {name} sign-in closed before it finished") from exc

    deadline = time.monotonic() + (DONE_WAIT_SEC if pasted else POLL_WAIT_SEC)
    while time.monotonic() < deadline:
        secret = cli.snapshot(session.kind, session.home)
        if secret is not None and _complete(secret):
            # A moment for the file beside it, which names the account, then all of it.
            time.sleep(SETTLE_SEC)
            secret = cli.snapshot(session.kind, session.home) or secret
            _drop(session_id)
            return SignedIn(secret, cli.label(session.kind, secret))
        if pasted and _REJECTED.search(session.text(screen=True)):
            # Claude Code ends once it has turned a code down, so the link is spent: a new one is needed.
            _drop(session_id)
            raise SignInError(f"{name} did not accept that code. Start again for a new link, and paste "
                              "the whole code it shows you.")
        if not session.reading:
            break
        time.sleep(0.2)
    if not pasted and session.reading:
        return None  # Codex: not entered on OpenAI's page yet
    tail = session.text(screen=True)[-1200:]
    logger.warning("signin: %s produced no login; screen tail=%r", session.kind, tail)
    _drop(session_id)
    raise SignInError(f"the {name} sign-in did not finish, please start again")


def cancel(session_id: str) -> None:
    """Drop a sign-in the person backed out of, rather than leaving it to the TTL."""
    _drop(session_id)


def reap(now: float | None = None) -> int:
    """End every sign-in past its TTL. Called on each start, so no timer thread is needed."""
    clock = time.monotonic() if now is None else now
    expired = [key for key, each in _SESSIONS.items() if clock - each.started_at > SESSION_TTL_SEC]
    for key in expired:
        _drop(key)
    return len(expired)


def live() -> int:
    return len(_SESSIONS)


def alive(session_id: str) -> bool:
    """Whether a sign-in can still take a code: one refused, finished or expired has ended."""
    return session_id in _SESSIONS


def _complete(secret: str) -> bool:
    """Whether the login file a tool wrote is whole yet, rather than half written."""
    import json  # noqa: PLC0415

    try:
        first = next(iter(json.loads(secret).values()))
        return bool(json.loads(first))
    except (ValueError, StopIteration):
        return False


def _await(session: _Session, pattern: re.Pattern[str], wait: float, *, screen: bool = False) -> str:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        found = pattern.search(session.text(screen=screen))
        if found:
            return found.group(0)
        if not session.reading:
            break  # the tool ended without printing it
        time.sleep(0.1)
    logger.warning("signin: %s printed no %s; screen tail=%r", session.kind,
                   "code" if screen else "link", session.text(screen=True)[-600:])
    raise SignInError(f"the {cli.TOOLS[session.kind].name} sign-in did not respond in time, please try again")


def _drop(session_id: str) -> None:
    session = _SESSIONS.pop(session_id, None)
    if session is not None:
        _end(session)


def _end(session: _Session) -> None:
    """End a sign-in's process and delete its folder, login files and all. Safe to call twice."""
    session.reading = False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(session.pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(session.pid, os.WNOHANG)
    with contextlib.suppress(OSError):
        os.close(session.fd)
    shutil.rmtree(session.home, ignore_errors=True)
