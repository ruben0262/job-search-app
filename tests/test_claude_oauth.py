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

from services.api import claude_oauth

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="pty.fork is POSIX only")

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


def test_start_returns_the_url_and_finish_returns_the_token(fake_cli):
    session_id, url = claude_oauth.start(USER)
    assert url.startswith("https://claude.ai/oauth/authorize?")
    assert claude_oauth.finish(session_id, USER, "goodcode").startswith("sk-ant-oat01-")


def test_a_finished_session_cannot_be_reused(fake_cli):
    session_id, _ = claude_oauth.start(USER)
    claude_oauth.finish(session_id, USER, "goodcode")
    with pytest.raises(claude_oauth.OAuthError, match="expired"):
        claude_oauth.finish(session_id, USER, "goodcode")


def test_a_bad_code_fails_without_echoing_the_cli_output(fake_cli):
    """**The message must not carry process output.** That output is where a token would be, and an
    error string is the one place a partial credential reaches a user's screen."""
    session_id, _ = claude_oauth.start(USER)
    with pytest.raises(claude_oauth.OAuthError) as caught:
        claude_oauth.finish(session_id, USER, "wrongcode")
    assert "Invalid code supplied" not in str(caught.value)
    assert "sk-ant" not in str(caught.value)


def test_another_user_cannot_finish_someone_elses_session(fake_cli):
    session_id, _ = claude_oauth.start(USER)
    other = __import__("uuid").uuid4()
    with pytest.raises(claude_oauth.OAuthError):
        claude_oauth.finish(session_id, other, "goodcode")


def test_starting_again_abandons_the_previous_attempt(fake_cli):
    """A user who reloads the modal must not leave a process blocked on a read forever."""
    first, _ = claude_oauth.start(USER)
    second, _ = claude_oauth.start(USER)
    assert first not in claude_oauth._SESSIONS
    assert second in claude_oauth._SESSIONS


def test_expired_sessions_are_reaped(fake_cli, monkeypatch):
    session_id, _ = claude_oauth.start(USER)
    monkeypatch.setattr(claude_oauth, "SESSION_TTL_SEC", 0.0)
    assert claude_oauth.reap(time.monotonic() + 1) == 1
    assert session_id not in claude_oauth._SESSIONS


def test_a_missing_cli_fails_as_an_oauth_error_not_a_crash(monkeypatch):
    """The CLI is absent in any image that did not install it, and that must read as a service
    problem rather than a 500 with a traceback."""
    monkeypatch.setattr(claude_oauth, "CLI", "/nonexistent/claude")
    with pytest.raises(claude_oauth.OAuthError):
        claude_oauth.start(USER)
