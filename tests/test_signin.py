"""Signing in to a person's own AI account through its provider's own tool (`libs.signin`, owner 2026-11-09).

Each provider's tool is a stand-in here that behaves as the real one was seen to, on 2026-11-09 and
2026-11-10, in a terminal of its own: Claude Code prints its link inside a terminal hyperlink, reads the
code as a plain line split at "#" (Node's `readline.createInterface({input})`, where the markers of a
bracketed paste stay part of the code) and exits once it has refused one; Gemini CLI asks for the code on
a plain line and keeps running once signed in; Codex shows a one-time code and writes its login when the
person has entered it on OpenAI's page. Each writes the login files the real one writes, into the folder
the sign-in gave it.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

import pytest

from libs import signin

posix = pytest.mark.skipif(not hasattr(os, "fork"), reason="pty.fork is POSIX only")

USER = uuid.uuid4()
CLAUDE_LINK = "https://claude.ai/oauth/authorize?code=true&client_id=9d1c&state=abc"

FAKE = """#!PYTHON
import base64, json, os, sys, time
args = sys.argv[1:]
def line():
    got = sys.stdin.readline()
    return got.replace("\\r", "").replace("\\n", "")
def write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as out:
        out.write(json.dumps(value))
if args[:2] == ["auth", "login"]:  # Claude Code
    folder = os.environ["CLAUDE_CONFIG_DIR"]
    # The link inside an OSC-8 hyperlink, its visible text wrapped mid-way: only the raw stream has it whole.
    print("Browser didn't open? Use the url below to sign in:")
    print("\\x1b]8;;" + LINK + "\\x07" + LINK[:30] + "\\n" + LINK[30:] + "\\x1b]8;;\\x07")
    print("Paste code here if prompted > ", end="", flush=True)
    while True:
        code, _, state = line().strip().partition("#")
        if code and state:
            break
        print("Invalid code. Please make sure the full code was copied.", flush=True)
    if (code, state) != ("good", "code"):  # the paste markers too, which arrive as part of it
        print("Login failed: That code wasn't accepted. Sign in again to get a new code.", flush=True)
        sys.exit(1)
    write(folder + "/.credentials.json", {"claudeAiOauth": {
        "accessToken": "sk-ant-oat01-" + "a" * 40, "refreshToken": "sk-ant-ort01-" + "r" * 40,
        "subscriptionType": "max"}})
    write(folder + "/.claude.json", {"oauthAccount": {"emailAddress": "ruben@example.com"},
                                     "inherited": os.environ.get("ANTHROPIC_API_KEY")})
    print("Login successful.", flush=True)
elif args[:1] == ["login"]:  # Codex
    folder = os.environ["CODEX_HOME"]
    print("1. Open this link in your browser and sign in to your account")
    print("   \\x1b[94mhttps://auth.openai.com/codex/device\\x1b[0m")
    print("2. Enter this one-time code \\x1b[90m(expires in 15 minutes)\\x1b[0m")
    print("   \\x1b[94mABCD-EF12\\x1b[0m", flush=True)
    while not os.path.exists(folder + "/entered"):  # the test says when it was entered on OpenAI's page
        time.sleep(0.1)
    claims = {"email": "ruben@example.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}
    token = "x." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".y"
    write(folder + "/auth.json", {"tokens": {"id_token": token, "access_token": "at", "refresh_token": "rt"}})
else:  # Gemini CLI
    home = os.environ["HOME"]
    print("Please visit the following URL to authorize the application:")
    print("")
    print("https://accounts.google.com/o/oauth2/v2/auth?redirect_uri=https%3A%2F%2Fcodeassist.google.com")
    print("")
    print("Enter the authorization code: ", end="", flush=True)
    code = line()
    if code == "4/good-code":  # a plain line: the brackets of a paste would be part of it
        write(home + "/.gemini/oauth_creds.json", {"access_token": "ya29.x", "refresh_token": "1//r"})
        write(home + "/.gemini/google_accounts.json", {"active": "ruben@gmail.com"})
    else:
        print("Failed to authenticate with authorization code.", flush=True)
    time.sleep(60)  # the interactive screen it goes on to, once signed in
""".replace("LINK", json.dumps(CLAUDE_LINK)).replace("#!PYTHON", f"#!{sys.executable}")


@pytest.fixture()
def tools(tmp_path, monkeypatch):
    """The three stand-ins, found the way the real tools are, by the variable naming each."""
    script = tmp_path / "faketool"
    script.write_text(FAKE)
    script.chmod(0o755)
    for variable in ("CLAUDE_CLI", "CODEX_CLI", "GEMINI_CLI"):
        monkeypatch.setenv(variable, str(script))
    for name, value in (("PASTE_SETTLE_SEC", 0.1), ("SETTLE_SEC", 0.1), ("POLL_WAIT_SEC", 1.0)):
        monkeypatch.setattr(signin, name, value)
    yield script
    for key in list(signin._SESSIONS):
        signin.cancel(key)


@posix
def test_claudes_link_is_taken_whole_and_a_pasted_code_links_the_account(tools, monkeypatch):
    """The link from the hyperlink, not the wrapped text the screen shows; the login files as the secret,
    named by the plan and address in them; and nothing of the server's environment reaches the tool."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "leak" * 10)

    started = signin.start(USER, "claude_login")
    folder = signin._SESSIONS[started.session_id].home
    done = signin.finish(started.session_id, USER, "good#code")

    assert (started.url, started.code) == (CLAUDE_LINK, None)
    files = json.loads(done.secret)
    assert set(files) == {".credentials.json", ".claude.json"}
    assert json.loads(files[".claude.json"])["inherited"] is None, "an API key in the server's environment"
    assert done.label == "Claude Max · ruben@example.com"
    assert not folder.exists(), "the login files are gone from disk once handed over"
    assert started.session_id not in signin._SESSIONS


@posix
def test_the_code_reaches_claude_as_plain_text_and_enter_on_its_own(tools, monkeypatch):
    """Sent as a bracketed paste, the markers became part of the code and Claude refused every one
    (2026-11-10, the owner's own sign-in)."""
    writes: list[bytes] = []
    real = os.write
    monkeypatch.setattr(signin.os, "write", lambda fd, data: writes.append(data) or real(fd, data))

    started = signin.start(USER, "claude_login")
    done = signin.finish(started.session_id, USER, " good#code ")

    assert writes[:2] == [b"good#code", b"\r"]
    assert done.label.startswith("Claude Max")


@posix
def test_a_refused_code_ends_the_sign_in_and_says_to_start_again(tools):
    """Claude Code exits once it has refused a code, so the same link cannot take another."""
    started = signin.start(USER, "claude_login")
    folder = signin._SESSIONS[started.session_id].home

    with pytest.raises(signin.SignInError, match="Start again for a new link"):
        signin.finish(started.session_id, USER, "wrong#code")

    assert not signin.alive(started.session_id) and not folder.exists()


@posix
def test_a_refused_code_is_logged_with_what_the_tool_said_and_never_the_code(tools, caplog):
    """The broker's log is the only view of a sign-in on the server (CLAUDE.md §8): why the provider
    turned a code down, in its own words, and what the code looked like; a code signs a person in until it
    is used, so it is never written down."""
    caplog.set_level("INFO", logger="libs.signin")
    started = signin.start(USER, "claude_login")

    with pytest.raises(signin.SignInError):
        signin.finish(started.session_id, USER, "wrongcodewrong#statestate")

    logged = caplog.text
    assert "signin: claude_login started" in logged and "back to -" in logged
    assert "code typed in (25 chars, 14 + 10 around '#')" in logged
    assert "That code wasn't accepted" in logged, "the tool's own words"
    assert "wrongcodewrong" not in logged and "statestate" not in logged


@posix
def test_a_code_that_does_not_look_like_one_keeps_the_sign_in(tools):
    started = signin.start(USER, "claude_login")

    with pytest.raises(signin.SignInError, match="does not look right"):
        signin.finish(started.session_id, USER, "a code with spaces")

    assert signin.alive(started.session_id)
    assert signin.finish(started.session_id, USER, "good#code").label.startswith("Claude Max")


@posix
def test_googles_code_goes_in_as_a_plain_line_and_its_login_is_kept(tools):
    """Gemini CLI asks on a plain line, where paste brackets would arrive as part of the code; and it
    keeps running once signed in, so signed in is its login file, not its exit."""
    started = signin.start(USER, "gemini_login")
    folder = signin._SESSIONS[started.session_id].home
    done = signin.finish(started.session_id, USER, "4/good-code")

    assert started.url.startswith("https://accounts.google.com/o/oauth2/")
    assert set(json.loads(done.secret)) == {".gemini/oauth_creds.json", ".gemini/google_accounts.json"}
    assert done.label == "Google Gemini · ruben@gmail.com"
    assert not folder.exists()


@posix
def test_chatgpt_shows_its_code_here_and_is_waited_for_until_openai_has_it(tools):
    started = signin.start(USER, "codex_login")
    folder = signin._SESSIONS[started.session_id].home

    assert (started.url, started.code) == ("https://auth.openai.com/codex/device", "ABCD-EF12")
    assert signin.finish(started.session_id, USER, None) is None, "not entered on OpenAI's page yet"
    (folder / "entered").touch()
    done = signin.finish(started.session_id, USER, None)

    assert done is not None and done.label == "ChatGPT Plus · ruben@example.com"
    assert set(json.loads(done.secret)) == {"auth.json"}


@posix
def test_another_person_cannot_finish_someone_elses_sign_in(tools):
    started = signin.start(USER, "claude_login")

    with pytest.raises(signin.SignInError, match="expired"):
        signin.finish(started.session_id, uuid.uuid4(), "good#code")
    assert signin.finish(started.session_id, USER, "good#code").label.startswith("Claude")


@posix
def test_starting_again_abandons_the_last_attempt_and_its_folder(tools):
    first = signin.start(USER, "claude_login")
    folder = signin._SESSIONS[first.session_id].home

    signin.start(USER, "gemini_login")

    assert first.session_id not in signin._SESSIONS and not folder.exists()


@posix
def test_a_sign_in_left_past_its_time_is_ended_and_its_folder_deleted(tools):
    started = signin.start(USER, "claude_login")
    folder = signin._SESSIONS[started.session_id].home

    assert signin.reap(now=time.monotonic() + signin.SESSION_TTL_SEC + 1) == 1
    assert not folder.exists()
    with pytest.raises(signin.SignInError, match="expired"):
        signin.finish(started.session_id, USER, "good#code")


@posix
def test_a_tool_that_is_not_installed_is_a_sign_in_error_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CLI", str(tmp_path / "no-such-tool"))
    monkeypatch.setattr(signin, "URL_WAIT_SEC", 2.0)

    with pytest.raises(signin.SignInError, match="did not respond"):
        signin.start(USER, "claude_login")
    assert not signin._SESSIONS


def test_there_is_no_sign_in_for_a_kind_that_is_not_one():
    with pytest.raises(signin.SignInError):
        signin.start(USER, "claude_api")


@posix
def test_the_broker_answers_gone_once_a_sign_in_has_ended_and_not_before(tools):
    """The page offers a new link on a 410, and keeps the same one on a 400."""
    from fastapi.testclient import TestClient

    from services.oauthbroker.__main__ import app

    with TestClient(app) as http:
        started = http.post("/start", json={"user_id": str(USER), "kind": "claude_login"}).json()
        ask = {"user_id": str(USER), "session_id": started["session_id"]}

        assert http.post("/finish", json={**ask, "code": "a code with spaces"}).status_code == 400
        assert http.post("/finish", json={**ask, "code": "wrong#code"}).status_code == 410
