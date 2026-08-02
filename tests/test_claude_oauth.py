"""The setup-token session driver.

These use a **fake CLI** rather than the real one: a tiny script that prints an authorization URL,
waits on stdin, and prints a token. That exercises the part that has actually broken in this kind of
code — pty handling, the session lifetime, the reaper — without needing a Claude subscription or a
browser in CI.

POSIX only. `pty.fork` has no Windows equivalent, and the API runs in a Linux container.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

from libs import claudeauth as claude_oauth

# Only the tests that drive a real process need this. The output-parsing tests below are pure
# string work and are the ones that caught the live failure, so they must run everywhere.
posix = pytest.mark.skipif(not hasattr(os, "fork"), reason="pty.fork is POSIX only")

USER = __import__("uuid").uuid4()

FAKE_CLI = """#!/usr/bin/env python3
import sys
print("Open this URL: https://claude.ai/oauth/authorize?code=1&state=abc")
sys.stdout.flush()
while True:
    line = sys.stdin.readline().strip()
    line = line.replace("\x1b[200~", "").replace("\x1b[201~", "")
    if not line:
        continue
    if line == "goodcode":
        print("Token: sk-ant-oat01-" + "x" * 40)
        sys.stdout.flush()
        break
    # What the real CLI does: report and stay up for another attempt.
    print("OAuth error: Invalid code. Please make sure the full code was copied")
    print("Press Enter to retry.")
    sys.stdout.flush()
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "fakeclaude"
    script.write_text(FAKE_CLI.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    script.chmod(0o755)
    monkeypatch.setattr(claude_oauth, "CLI", str(script))
    yield script
    for key in list(claude_oauth._SESSIONS):
        claude_oauth.cancel(key)


@posix
def test_start_returns_the_url_and_finish_returns_the_token(fake_cli):
    session_id, url = claude_oauth.start(USER)
    assert url.startswith("https://claude.ai/oauth/authorize?")
    assert claude_oauth.finish(session_id, USER, "goodcode").startswith("sk-ant-oat01-")


@posix
def test_a_finished_session_cannot_be_reused(fake_cli):
    session_id, _ = claude_oauth.start(USER)
    claude_oauth.finish(session_id, USER, "goodcode")
    with pytest.raises(claude_oauth.OAuthError, match="expired"):
        claude_oauth.finish(session_id, USER, "goodcode")


@posix
def test_a_bad_code_fails_without_echoing_the_cli_output(fake_cli):
    """**The message must not carry process output.** That output is where a token would be, and an
    error string is the one place a partial credential reaches a user's screen."""
    session_id, _ = claude_oauth.start(USER)
    with pytest.raises(claude_oauth.OAuthError) as caught:
        claude_oauth.finish(session_id, USER, "wrongcode")
    assert "Invalid code supplied" not in str(caught.value)
    assert "sk-ant" not in str(caught.value)


@posix
def test_another_user_cannot_finish_someone_elses_session(fake_cli):
    session_id, _ = claude_oauth.start(USER)
    other = __import__("uuid").uuid4()
    with pytest.raises(claude_oauth.OAuthError):
        claude_oauth.finish(session_id, other, "goodcode")


@posix
def test_starting_again_abandons_the_previous_attempt(fake_cli):
    """A user who reloads the modal must not leave a process blocked on a read forever."""
    first, _ = claude_oauth.start(USER)
    second, _ = claude_oauth.start(USER)
    assert first not in claude_oauth._SESSIONS
    assert second in claude_oauth._SESSIONS


@posix
def test_expired_sessions_are_reaped(fake_cli, monkeypatch):
    session_id, _ = claude_oauth.start(USER)
    monkeypatch.setattr(claude_oauth, "SESSION_TTL_SEC", 0.0)
    assert claude_oauth.reap(time.monotonic() + 1) == 1
    assert session_id not in claude_oauth._SESSIONS


@posix
def test_a_missing_cli_fails_as_an_oauth_error_not_a_crash(monkeypatch):
    """The CLI is absent in any image that did not install it, and that must read as a service
    problem rather than a 500 with a traceback."""
    monkeypatch.setattr(claude_oauth, "CLI", "/nonexistent/claude")
    with pytest.raises(claude_oauth.OAuthError):
        claude_oauth.start(USER)


# A verbatim fragment of what claude-code 2.1.220 actually wrote to the pty, kept because every
# assumption this module made about that output was wrong on the first live run: the host is
# `claude.com/cai/`, not `claude.ai/`, and the complete URL is only inside the OSC-8 hyperlink --
# the visible copy beside it is word-wrapped and truncated. Both cost a deploy.
REAL_OUTPUT = (
    "\x1b[2G\xb7\x1b[4GOpening\x1b[12Gbrowser\x1b[20Gto\x1b[23Gsign\x1b[28Gin\u2026\r\r\n"
    "Browser didn't open?\x1b[23GUse the url\x1b[35Gbelow\r\r\n"
    "\x1b]8;id=nyh2h8;https://claude.com/cai/oauth/authorize?code=true"
    "&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code"
    "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
    "&scope=user%3Ainference&code_challenge=mY568CUpD3FSmN607OgWXYZ0RVOFmquYqjX9j9XWtJo"
    "&code_challenge_method=S256&state=J8UunON0ObWlK_yxckyOVc5BN0xaZfonDBcteJxD5H8\x07"
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88\x1b]8;;\x07\r\n"
)


def test_the_url_is_taken_whole_from_the_hyperlink_not_the_wrapped_text():
    found = claude_oauth._URL.search(REAL_OUTPUT)
    assert found, "the URL the CLI actually prints must match"
    url = found.group(0)
    assert url.startswith("https://claude.com/cai/oauth/authorize?")
    # The visible copy stops mid-parameter; the real one carries the verifier challenge and state.
    assert url.endswith("state=J8UunON0ObWlK_yxckyOVc5BN0xaZfonDBcteJxD5H8")
    assert "code_challenge=" in url


def test_a_token_split_by_cursor_moves_is_still_found():
    """The CLI redraws as it writes, so escapes land mid-string. Stripping them is what makes the
    token match survive a line the terminal decided to re-position."""
    noisy = "Token:\x1b[12G sk-ant-oat01-" + "A" * 20 + "\x1b[2G\x1b[K" + "B" * 20 + "\r\n"
    cleaned = claude_oauth._ESCAPES.sub("", noisy)
    assert claude_oauth._TOKEN.search(cleaned).group(0) == "sk-ant-oat01-" + "A" * 20 + "B" * 20


def test_a_rejection_is_recognised_from_what_the_cli_really_prints():
    """Verbatim from claude-code 2.1.220 answering a bad code, escapes and all. Matched so the
    rejection is reported the moment it appears rather than after the read timeout."""
    rejected = ("\x1b[1A\rOAuth error: Invalid\x1b[23Gcode. Please make\x1b[41Gsure the full"
                "\x1b[55Gc\x1b[57Gde was\x1b[64Gcopied\r\x1b[2B\x1b[K\rPress\x1b[8GEnter"
                "\x1b[14Gto\x1b[17Gretry.\r")
    cleaned = claude_oauth._ESCAPES.sub("", rejected)
    found = claude_oauth._OUTCOME.search(cleaned)
    assert found and not found.group(0).startswith("sk-ant-oat")


@posix
def test_a_rejected_code_keeps_the_session_so_the_same_link_still_works(fake_cli):
    """**The bug this replaces made the flow unescapable.** A wrong code killed the session, the
    next attempt minted a new PKCE challenge, and the code in the tab the user had already
    authorized in could then never be accepted."""
    session_id, first_url = claude_oauth.start(USER)
    with pytest.raises(claude_oauth.OAuthError, match="did not accept"):
        claude_oauth.finish(session_id, USER, "wrongcode")
    assert session_id in claude_oauth._SESSIONS, "the link the user holds must stay valid"
    assert first_url


# A CLI that keeps writing while it waits, which is what the real one does: it animates a spinner
# the entire time the user is in their browser.
CHATTY_CLI = """#!/usr/bin/env python3
import sys, threading, time
print("Open this URL: https://claude.ai/oauth/authorize?code=1&state=abc")
sys.stdout.flush()

def spin():
    while True:
        sys.stdout.write("." * 512)
        sys.stdout.flush()
        time.sleep(0.01)

threading.Thread(target=spin, daemon=True).start()
line = sys.stdin.readline().strip()
line = line.replace("\x1b[200~", "").replace("\x1b[201~", "")
if line == "goodcode":
    print("Token: sk-ant-oat01-" + "x" * 40)
sys.stdout.flush()
time.sleep(5)
"""


@posix
def test_a_chatty_cli_does_not_wedge_while_the_user_is_in_the_browser(tmp_path, monkeypatch):
    """**The bug this pins down.** Nothing drained the pty between `start` and `finish`, so a CLI
    that writes while it waits filled the kernel buffer, blocked in `write`, and stopped reading its
    input. The code we sent was never processed and the wait ran to its full timeout -- and the
    longer the user spent signing in, the more certain that became.

    Without the reader thread this test hangs until the deadline and fails; with it, it passes in
    about a second.
    """
    script = tmp_path / "chatty"
    script.write_text(CHATTY_CLI.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    script.chmod(0o755)
    monkeypatch.setattr(claude_oauth, "CLI", str(script))

    session_id, _ = claude_oauth.start(USER)
    # Long enough that the pty buffer is well past full if nothing is reading it.
    time.sleep(2.0)
    started = time.monotonic()
    token = claude_oauth.finish(session_id, USER, "goodcode")
    assert token.startswith("sk-ant-oat01-")
    assert time.monotonic() - started < 15, "the code must be answered promptly, not after a timeout"


@posix
def test_the_code_is_delivered_as_a_bracketed_paste(fake_cli, monkeypatch):
    """**The failure that looked exactly like a hang.** Written as one burst of `code + "\r"`, the
    real CLI dropped eight of ninety-two characters and swallowed the carriage return, so the code
    sat half-entered on the prompt and nothing was ever submitted. Nothing timed out on its side and
    nothing crashed; it was still waiting for input we believed we had sent.

    Asserted on the bytes rather than against a stand-in CLI, because what has to hold is the wire
    protocol: the value arrives announced as a paste, and Enter arrives as a keypress of its own.
    """
    writes: list[bytes] = []
    real_write = os.write

    def record(fd, data):
        writes.append(data)
        return real_write(fd, data)

    monkeypatch.setattr(claude_oauth.os, "write", record)
    session_id, _ = claude_oauth.start(USER)
    claude_oauth.finish(session_id, USER, "goodcode")

    assert writes[0].startswith(b"\x1b[200~"), "the code must be announced as a paste"
    assert writes[0].endswith(b"\x1b[201~"), "and closed, or the CLI keeps waiting for more"
    assert b"goodcode" in writes[0]
    # Enter is its own write. Bundled into the burst above, the CLI's paste debounce ate it.
    assert writes[1] == b"\r"


def test_the_screen_reassembles_a_token_the_cli_draws_by_cell():
    """**The bug that stored a broken credential.** The CLI renders by cell: it writes `sk-ant-o`,
    jumps the cursor, writes the rest, and leaves characters an earlier draw already placed. Deleting
    the escape sequences concatenated only what was written, so `sk-ant-oat01-...` was captured as
    `sk-ant-at01-...` -- plausible, pattern-matching, and rejected by Anthropic.

    Rendered on a screen that holds state, the character is where the user can see it.
    """
    import pyte

    screen = pyte.Screen(120, 8)
    stream = pyte.Stream(screen)
    # A full line, then a redraw that repositions over it rather than rewriting every cell.
    stream.feed("sk-ant-oat01-" + "A" * 30 + "\r\n")
    stream.feed("\x1b[1;1H" + "sk-ant-" + "\x1b[1;9H" + "at01-" + "A" * 30)

    rendered = "\n".join(line.rstrip() for line in screen.display)
    assert "sk-ant-oat01-" in rendered, rendered[:60]
    # What the old approach produced from the same stream, and why it was stored.
    assert "sk-ant-at01-" in claude_oauth._ESCAPES.sub(
        "", "sk-ant-" + "\x1b[1;9H" + "at01-" + "A" * 30)


@posix
def test_clearing_keeps_the_screen_so_a_redraw_is_not_corrupted(fake_cli):
    """**Resetting the screen mid-session broke the token.** The CLI diffs against its own model of
    what it has drawn, so a cell it believes still holds a character is never rewritten. Wiping our
    copy left a space in its place: `sk-ant-oat01-` rendered as `sk-ant- at01-`, which matched
    nothing and was reported as no outcome at all.
    """
    session_id, _ = claude_oauth.start(USER)
    session = claude_oauth._SESSIONS[session_id]
    assert "claude.ai/oauth/authorize" in session.text(strip=True)

    session.clear()
    assert session.text() == "", "the raw stream is dropped"
    assert "claude.ai/oauth/authorize" in session.text(strip=True), "the screen is not"


def test_a_match_already_on_screen_is_not_mistaken_for_the_answer():
    """Since the screen is no longer wiped, a rejection from an earlier attempt stays visible. It
    must not be read as the reply to the code just sent."""
    stale = "OAuth error: Invalid code"
    fresh = "sk-ant-oat01-" + "Z" * 40
    # What the screen looks like on a retry: last attempt's rejection still there, this attempt's
    # token underneath it. Taking the first match would report the old failure forever.
    screen = f"{stale}\nPaste code here if prompted >\n{fresh}"

    assert claude_oauth._OUTCOME.search(screen).group(0) == "OAuth error:", "first match is stale"
    survivor = next(match.group(0) for match in claude_oauth._OUTCOME.finditer(screen)
                    if match.group(0) not in stale)
    assert survivor == fresh
