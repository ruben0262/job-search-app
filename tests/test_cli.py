"""Writing with a signed-in account: the account's own tool, run once per call (`libs.llm.cli`).

The tools are stand-ins answering as the real ones do with `-p` (Claude Code), `exec` (Codex) and `-p`
(Gemini CLI): Claude Code's JSON with `result` and `is_error`, Codex's last message in a file, Gemini
CLI's JSON with `response`. Each says back what reached it, and renews its login when told to.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from libs.llm import cli
from libs.llm.client import LlmError

FAKE = """#!PYTHON
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
home = os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("CODEX_HOME") or os.environ["HOME"]
def option(name):
    return args[args.index(name) + 1] if name in args else None
def renew(path):
    login = json.load(open(path))
    if login.get("renew"):
        login["renew"], login["token"] = False, "renewed"
        json.dump(login, open(path, "w"))
    return login
said = {"prompt": prompt, "cwd": os.getcwd(), "model": option("--model"), "args": args,
        "inherited": os.environ.get("ANTHROPIC_API_KEY"), "files": sorted(
            os.path.relpath(os.path.join(root, name), home)
            for root, _, names in os.walk(home) for name in names)}
if args[:1] == ["-p"]:  # Claude Code
    login = renew(home + "/.credentials.json")
    if login.get("expired"):
        print(json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}))
        sys.exit(1)
    said["system"] = open(option("--system-prompt-file")).read()
    print(json.dumps({"type": "result", "is_error": False, "result": json.dumps(said)}))
elif args[:1] == ["exec"]:  # Codex
    renew(home + "/auth.json")
    open(option("--output-last-message"), "w").write(json.dumps(said))
else:  # Gemini CLI
    renew(home + "/.gemini/oauth_creds.json")
    said["system"] = open(os.environ["GEMINI_SYSTEM_MD"]).read()
    print(json.dumps({"response": json.dumps(said), "stats": {}}))
""".replace("#!PYTHON", f"#!{sys.executable}")

LOGINS = {"claude_login": ".credentials.json", "codex_login": "auth.json",
          "gemini_login": ".gemini/oauth_creds.json"}


@pytest.fixture()
def tools(tmp_path, monkeypatch):
    script = tmp_path / "faketool"
    script.write_text(FAKE)
    script.chmod(0o755)
    for variable in ("CLAUDE_CLI", "CODEX_CLI", "GEMINI_CLI"):
        monkeypatch.setenv(variable, str(script))
    return script


def _blob(kind: str, **login) -> str:
    return json.dumps({LOGINS[kind]: json.dumps({"token": "first", **login})})


def _write(kind: str, blob: str, model: str = "the-model", renewed: list | None = None) -> dict:
    client = cli.CliClient(kind, blob, (renewed if renewed is not None else []).append)
    return json.loads(client.complete_text(model=model, system="Write CVs plainly.", max_tokens=4000,
                                           messages=[{"role": "user", "content": "The job: a nurse."}]))


@pytest.mark.parametrize("kind", ["claude_login", "codex_login", "gemini_login"])
def test_each_tool_is_given_the_prompt_and_its_answer_comes_back(tools, kind, monkeypatch):
    """And nothing of the server's environment: an API key there would sign the tool in as the server."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "leak" * 10)

    said = _write(kind, _blob(kind))

    assert "The job: a nurse." in said["prompt"]
    assert "Write CVs plainly." in (said.get("system") or said["prompt"]), "the platform's system prompt"
    assert said["model"] == "the-model" and said["inherited"] is None
    settings = [".gemini/settings.json"] if kind == "gemini_login" else []
    assert said["files"] == sorted([LOGINS[kind], *settings])
    assert not Path(said["cwd"]).exists(), "nothing of the call is left on disk"


def test_claude_runs_with_no_tools_and_gemini_trusted_and_read_only(tools):
    claude = _write("claude_login", _blob("claude_login"))["args"]
    gemini = _write("gemini_login", _blob("gemini_login"))["args"]

    assert claude[claude.index("--tools") + 1] == ""
    assert "--skip-trust" in gemini and gemini[gemini.index("--approval-mode") + 1] == "plan"


def test_a_claude_sign_in_thinks_as_hard_as_an_api_key_does(tools):
    """The docgen worker asks for one effort; left out, Claude Code thought at its own default, which
    nobody had chosen and an API key never ran at (2026-11-11)."""
    client = cli.CliClient("claude_login", _blob("claude_login"), [].append)
    said = json.loads(client.complete_text(model="m", system="s", max_tokens=1, effort="medium",
                                           messages=[{"role": "user", "content": "p"}]))

    assert said["args"][said["args"].index("--effort") + 1] == "medium"
    assert "--effort" not in _write("claude_login", _blob("claude_login"))["args"], "only when asked"


def test_codex_picks_its_own_model_when_none_is_set(tools):
    """A ChatGPT plan's models are Codex's to know; an API model's name may not be one of them."""
    assert _write("codex_login", _blob("codex_login"), model="")["model"] is None


@pytest.mark.parametrize("kind", ["claude_login", "codex_login", "gemini_login"])
def test_a_login_the_tool_renewed_is_handed_back_to_be_sealed_and_used_next(tools, kind):
    renewed: list[str] = []
    client = cli.CliClient(kind, _blob(kind, renew=True), renewed.append)
    ask = {"model": "m", "system": "s", "max_tokens": 1, "messages": [{"role": "user", "content": "p"}]}

    client.complete_text(**ask)
    client.complete_text(**ask)

    assert len(renewed) == 1, "handed back once: the second call ran on the renewed login and renewed nothing"
    assert json.loads(json.loads(renewed[0])[LOGINS[kind]])["token"] == "renewed"


def test_an_expired_sign_in_says_to_sign_in_again(tools):
    with pytest.raises(LlmError, match="sign in again in Settings"):
        _write("claude_login", _blob("claude_login", expired=True))


def test_only_the_files_a_sign_in_is_made_of_are_unpacked(tmp_path):
    """Whatever a stored secret holds, nothing is written outside the tool's own login files."""
    blob = json.dumps({".credentials.json": "{}", "../escaped.json": "x", "settings.json": "{\"hooks\": 1}"})
    home = tmp_path / "home"
    home.mkdir()

    cli.unpack("claude_login", blob, home)

    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == [".credentials.json"]
    assert oct((home / ".credentials.json").stat().st_mode)[-3:] == "600"


def test_a_tool_that_is_not_installed_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CLI", str(tmp_path / "no-such-tool"))

    with pytest.raises(LlmError, match="cannot be reached"):
        _write("claude_login", _blob("claude_login"))


def test_a_signed_in_account_writes_documents_and_nothing_else(tools):
    """The help chat calls tools, which these do not offer: it runs on API keys (`libs.llm.resolve`)."""
    with pytest.raises(LlmError, match="documents only"):
        cli.CliClient("claude_login", _blob("claude_login"), print).complete_tools(model="m")


def test_each_account_is_named_by_its_plan_and_address_where_the_sign_in_says_them():
    claims = base64.urlsafe_b64encode(json.dumps({
        "email": "ruben@example.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}}).encode())
    token = f"x.{claims.decode().rstrip('=')}.y"
    codex = json.dumps({"auth.json": json.dumps({"tokens": {"id_token": token}})})
    claude = json.dumps({".credentials.json": json.dumps({"claudeAiOauth": {"subscriptionType": "pro"}}),
                         ".claude.json": json.dumps({"oauthAccount": {"emailAddress": "a@example.com"}})})
    gemini = json.dumps({".gemini/oauth_creds.json": "{}",
                         ".gemini/google_accounts.json": json.dumps({"active": "b@gmail.com"})})

    assert cli.label("codex_login", codex) == "ChatGPT Pro · ruben@example.com"
    assert cli.label("claude_login", claude) == "Claude Pro · a@example.com"
    assert cli.label("gemini_login", gemini) == "Google Gemini · b@gmail.com"
    assert cli.label("claude_login", json.dumps({".credentials.json": "{}"})) == "Claude", "says nothing more"
