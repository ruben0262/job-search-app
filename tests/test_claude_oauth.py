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
line = sys.stdin.readline().strip()
if line == "goodcode":
    print("Token: sk-ant-oat01-" + "x" * 40)
else:
    print("Invalid code supplied")
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
