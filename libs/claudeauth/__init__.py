"""Driving `claude setup-token` on the user's behalf."""
from __future__ import annotations

import contextlib
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

# The URL the CLI prints, and the token it prints at the end.
_URL = re.compile(r"https://[^\s\x07\x1b]+/oauth/authorize\?[^\s\x07\x1b]+")
# Deliberately loose on the middle segment.
_TOKEN = re.compile(r"sk-ant-[A-Za-z0-9]{2,12}-[A-Za-z0-9_-]{20,}")

# What the CLI says when Claude turns the code down.
_REJECTED = re.compile(r"OAuth error:|Invalid code|Request failed"
                       r"|Press\s+Enter\s+to\s+retry")

# Either outcome. `finish` decides which happened by looking at what matched.
_OUTCOME = re.compile(_TOKEN.pattern + "|" + _REJECTED.pattern)

# Cursor moves, colours, and OSC sequences.
_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
                      r"|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")

# How long a user has between "open this link" and pasting the code back.
SESSION_TTL_SEC = 15 * 60

# Reading the URL takes a second or two; the token after the code is a network round trip.
URL_WAIT_SEC = 45.0
TOKEN_WAIT_SEC = 60.0

# How long the CLI is given to take the pasted run in before Enter is delivered.
PASTE_SETTLE_SEC = 0.8

# A ceiling on live sessions so a scripted caller cannot fork the API host to death.
MAX_LIVE_SESSIONS = 24

CLI = os.environ.get("CLAUDE_CLI", "claude")

logger = logging.getLogger(__name__)


class OAuthError(RuntimeError):
    """A setup-token run that cannot be completed."""


# The most a session's transcript is allowed to grow to.
MAX_BUFFER = 64 * 1024

# The emulated terminal.
SCREEN_COLS = 500
SCREEN_ROWS = 100


class _Session:
    """A live `setup-token` process, with something continuously reading its output."""

    __slots__ = (
        "_buf",
        "_lock",
        "_reading",
        "_screen",
        "_stream",
        "fd",
        "pid",
        "started_at",
        "user_id",
    )

    def __init__(self, pid: int, fd: int, user_id: _uuid.UUID) -> None:
        self.pid = pid
        self.fd = fd
        self.started_at = time.monotonic()
        self.user_id = user_id
        self._buf = ""
        # A real terminal, because deleting the escapes silently corrupts the text.
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
                # Raw, for the URL: it arrives inside an OSC-8 hyperlink, so the screen never sees it.
                self._buf = (self._buf + text)[-MAX_BUFFER:]
                self._stream.feed(text)
        self._reading = False

    def text(self, *, strip: bool = False) -> str:
        """The raw stream, or what the terminal is actually displaying."""
        with self._lock:
            if not strip:
                return self._buf
            return chr(10).join(line.rstrip() for line in self._screen.display)

    def clear(self) -> None:
        """Forget the raw stream."""
        with self._lock:
            self._buf = ""

    def stop(self) -> None:
        self._reading = False


# Process-local.
_SESSIONS: dict[str, _Session] = {}


def _await(session: _Session, pattern: re.Pattern[str], deadline: float, *,
           strip: bool = False, ignore: str = "") -> str:
    """Wait for `pattern` to appear in what the session has read."""
    while time.monotonic() < deadline:
        for found in pattern.finditer(session.text(strip=strip)):
            # A match already on screen before we sent anything is last time's answer, not a reply to this.
            if found.group(0) not in ignore:
                return found.group(0)
        if not session._reading:
            # The process ended without printing what we were waiting for.
            break
        time.sleep(0.1)
    raise OAuthError("the Claude sign-in did not respond in time, please try again")


def _kill(session: _Session) -> None:
    """End a session's process and release its descriptor. Safe to call twice."""
    session.stop()
    # Every step is best-effort: the process may already be gone, reaped, or its descriptor closed.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(session.pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(session.pid, os.WNOHANG)
    with contextlib.suppress(OSError):
        os.close(session.fd)


def reap(now: float | None = None) -> int:
    """Kill every session past its TTL. Called on each start, so no timer thread is needed."""
    clock = time.monotonic() if now is None else now
    expired = [key for key, s in _SESSIONS.items() if clock - s.started_at > SESSION_TTL_SEC]
    for key in expired:
        _kill(_SESSIONS.pop(key))
    return len(expired)


def start(user_id: _uuid.UUID) -> tuple[str, str]:
    """Spawn `claude setup-token` and return (session_id, authorization_url)."""
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
        # No inherited credential may leak into the child: a token in the environment skips the flow.
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        env["TERM"] = "dumb"
        env["COLUMNS"] = "500"
        env["NO_COLOR"] = "1"
        try:
            # No shell, deliberately: the argument list is fixed here and nothing a user supplies reaches it.
            os.execvpe(CLI, [CLI, "setup-token"], env)  # noqa: S606
        finally:
            os._exit(127)

    os.set_blocking(fd, False)
    # A wide terminal, so the CLI never wraps.
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
    """Feed the browser's code to the waiting process and return the token it prints."""
    session = _SESSIONS.get(session_id)
    if session is None:
        raise OAuthError("that sign-in expired, please start again")
    if session.user_id != user_id:
        # Cannot happen through the routes, which look the session up for the caller.
        _SESSIONS.pop(session_id, None)
        _kill(session)
        raise OAuthError("that sign-in expired, please start again")

    # What is already on screen belongs to the prompt, or to an earlier attempt.
    session.clear()
    before = session.text(strip=True)
    try:
        # Bracketed paste, and Enter as a separate write.
        os.write(session.fd, b"\x1b[200~" + code.strip().encode() + b"\x1b[201~")
        time.sleep(PASTE_SETTLE_SEC)
        os.write(session.fd, b"\r")
        token = _await(session, _OUTCOME, time.monotonic() + TOKEN_WAIT_SEC,
                       strip=True, ignore=before)
    except OSError as exc:
        _SESSIONS.pop(session_id, None)
        _kill(session)
        raise OAuthError("the Claude sign-in closed before it finished") from exc
    except OAuthError:
        # Say what the CLI actually printed.
        transcript = _TOKEN.sub("<token>", session.text(strip=True))[-1200:]
        logger.warning("setup-token produced no recognised outcome; tail=%r", transcript)
        _SESSIONS.pop(session_id, None)
        _kill(session)
        raise OAuthError("the Claude sign-in did not answer, please start again") from None

    if not token.startswith("sk-ant-"):
        # Claude rejected it.
        with contextlib.suppress(OSError):
            os.write(session.fd, b"\r")
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
