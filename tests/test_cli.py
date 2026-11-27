"""Writing with a signed-in account: the account's own tool, run once per call (`libs.llm.cli`).

The tools are stand-ins answering as the real ones do with `-p` (Claude Code), `exec` (Codex) and `-p`
(Gemini CLI): Claude Code's JSON with `result` and `is_error`, Codex's last message in a file, Gemini
CLI's JSON with `response`. Each says back what reached it, and renews its login when told to.
"""
from __future__ import annotations

import base64
import contextlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from libs.llm import cli
from libs.llm.client import LlmError, Usage

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
    print(json.dumps({"type": "result", "is_error": False, "result": json.dumps(said), "usage": {
        "input_tokens": 12, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 500,
        "output_tokens": 900}, "modelUsage": {"claude-haiku-x": {"outputTokens": 900}}}))
elif args[:1] == ["exec"]:  # Codex, `exec --json`
    login = renew(home + "/auth.json")
    if login.get("limit"):
        print(json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}))
        sys.exit(1)
    settings = [args[at + 1] for at, arg in enumerate(args) if arg == "-c"]
    told = [each.split("=", 1)[1] for each in settings if each.startswith("model_instructions_file=")]
    if told:
        said["system"] = open(json.loads(told[0])).read()
    open(option("--output-last-message"), "w").write(json.dumps(said))
    print(json.dumps({"type": "thread.started", "thread_id": "t"}))
    print("not an event")
    used = {"input_tokens": 9800, "cached_input_tokens": 4800, "output_tokens": 640,
            "reasoning_output_tokens": 300}
    print(json.dumps({"type": "turn.completed", "usage": used}))
else:  # Gemini CLI
    login = renew(home + "/.gemini/oauth_creds.json")
    if login.get("retired"):
        sys.stderr.write("Warning: Basic terminal.\\nError authenticating: IneligibleTierError: This "
                         "client is no longer supported for Gemini Code Assist for individuals.\\n"
                         + "    at _doSetupUser (file:///bundle/chunk.js:312752:5)\\n" * 6)
        sys.exit(1)
    said["system"] = open(os.environ["GEMINI_SYSTEM_MD"]).read()
    print(json.dumps({"response": json.dumps(said), "stats": {"models": {"gemini-x": {"tokens": {
        "prompt": 4100, "candidates": 700, "thoughts": 250, "total": 5050}}}}}))
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


def test_claude_searches_the_web_only_when_the_bench_asks_and_never_fetches_a_page(tools, monkeypatch):
    """Production writes from what it is given. The bench may let Claude search, to measure it (owner,
    2026-11-16); fetching stays off either way: a posting's words could point it at a private address."""
    plain = _write("claude_login", _blob("claude_login"))["args"]
    assert plain[plain.index("--tools") + 1] == "" and "--allowedTools" not in plain
    monkeypatch.setattr(cli, "WEB_SEARCH", True)
    told = _write("claude_login", _blob("claude_login"))["args"]
    assert told[told.index("--tools") + 1] == "WebSearch"
    assert told[told.index("--allowedTools") + 1] == "WebSearch"
    assert "WebFetch" not in " ".join(told)


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


def test_codex_is_told_how_hard_to_think_only_by_the_bench(tools, monkeypatch):
    """A ChatGPT sign-in writes at Codex's own setting, as it has since it was added: an effort changes
    every ChatGPT user's documents, so it is measured first (owner, 2026-11-15): only the bench sets it."""
    assert "-c" not in _write("codex_login", _blob("codex_login"))["args"]

    monkeypatch.setattr(cli, "CODEX_EFFORT", "medium")
    told = _write("codex_login", _blob("codex_login"))["args"]
    assert told[told.index("-c") + 1] == "model_reasoning_effort=medium"
    assert "-c" not in _write("claude_login", _blob("claude_login"))["args"]


def test_codex_writes_lean_only_when_the_bench_asks_from_this_platforms_instructions_and_with_no_tools(
        tools, monkeypatch):
    """Owner, 2026-11-19: use less of a person's ChatGPT plan. Codex adds a coding agent's prompt and its
    tools to every call, 13,856 tokens for a one-word answer and 4,842 without them. Lean, the platform's
    instructions are Codex's own and nothing else is offered; plain, as every account writes until letters
    written lean are measured, they lead the prompt and Codex keeps its own."""
    plain = _write("codex_login", _blob("codex_login"))
    assert "Write CVs plainly." in plain["prompt"] and "system" not in plain
    assert not any(arg.startswith("features.") for arg in plain["args"])

    monkeypatch.setattr(cli, "CODEX_LEAN", True)
    lean = _write("codex_login", _blob("codex_login"))
    assert lean["system"] == "Write CVs plainly." and "Write CVs plainly." not in lean["prompt"]
    assert "The job: a nurse." in lean["prompt"]
    for setting in ('web_search="disabled"', "features.shell_tool=false", "features.multi_agent=false",
                    "features.browser_use=false", "features.image_generation=false"):
        assert setting in lean["args"], setting


def test_a_codex_limit_is_said_as_a_limit(tools):
    with pytest.raises(LlmError, match="usage limit"):
        _write("codex_login", _blob("codex_login", limit=True))


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


def test_an_answer_carries_what_its_call_used_where_the_tool_says(tools):
    """Owner, 2026-11-14: record the token spend. Claude Code and Gemini CLI report it with the answer;
    what went in is the prompt however it was billed, and what came out includes the thinking. Codex
    prints its events as JSON, the turn's use among them (2026-11-19)."""
    def used(kind: str):
        client = cli.CliClient(kind, _blob(kind), [].append)
        return client.complete_text(model="m", system="s", max_tokens=1,
                                    messages=[{"role": "user", "content": "p"}]).usage

    assert used("claude_login") == Usage(input_tokens=3512, output_tokens=900)
    assert used("gemini_login") == Usage(input_tokens=4100, output_tokens=950)
    assert used("codex_login") == Usage(input_tokens=9800, output_tokens=640)


def test_an_answer_says_which_model_wrote_it_where_the_tool_says(tools):
    """Owner, 2026-11-15: a document is kept by the model that produced it. That is the model the tool
    says answered, which need not be the one asked for: a plan may not include it. Codex's events name
    no model, so its document is kept by the one asked for, and by none when the plan chose."""
    def wrote(kind: str):
        client = cli.CliClient(kind, _blob(kind), [].append)
        return client.complete_text(model="asked-for", system="s", max_tokens=1,
                                    messages=[{"role": "user", "content": "p"}]).model

    assert wrote("claude_login") == "claude-haiku-x"
    assert wrote("gemini_login") == "gemini-x"
    assert wrote("codex_login") == "asked-for"


def test_an_expired_sign_in_says_to_sign_in_again(tools):
    with pytest.raises(LlmError, match="sign in again in Settings"):
        _write("claude_login", _blob("claude_login", expired=True))


def test_a_sign_in_its_provider_no_longer_takes_says_so_and_what_to_do(tools):
    """Google stopped taking a Gemini CLI sign-in from individuals ("IneligibleTierError: This client is no
    longer supported", seen 2026-11-15). Its reason is the first line of a long trace, which was cut off:
    the person read "it gave no reason this site understands", and "authenticating" alone would have told
    them to sign in again, which cannot help."""
    with pytest.raises(LlmError, match=r"no longer takes this kind of sign-in.*API key"):
        _write("gemini_login", _blob("gemini_login", retired=True))


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
        "email": "sam@example.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}}).encode())
    token = f"x.{claims.decode().rstrip('=')}.y"
    codex = json.dumps({"auth.json": json.dumps({"tokens": {"id_token": token}})})
    claude = json.dumps({".credentials.json": json.dumps({"claudeAiOauth": {"subscriptionType": "pro"}}),
                         ".claude.json": json.dumps({"oauthAccount": {"emailAddress": "a@example.com"}})})
    gemini = json.dumps({".gemini/oauth_creds.json": "{}",
                         ".gemini/google_accounts.json": json.dumps({"active": "b@gmail.com"})})

    assert cli.label("codex_login", codex) == "ChatGPT Pro · sam@example.com"
    assert cli.label("claude_login", claude) == "Claude Pro · a@example.com"
    assert cli.label("gemini_login", gemini) == "Google Gemini · b@gmail.com"
    assert cli.label("claude_login", json.dumps({".credentials.json": "{}"})) == "Claude", "says nothing more"


def _signed_in(expires_in: float) -> str:
    """A Claude sign-in whose session runs out `expires_in` seconds from now, kept as `cli.snapshot` does."""
    expires_at = (time.time() + expires_in) * 1000
    return json.dumps({".credentials.json": json.dumps({"claudeAiOauth": {"expiresAt": expires_at}}),
                       ".claude.json": "{}"}, sort_keys=True)


def test_a_sign_in_says_when_its_session_runs_out_and_one_that_does_not_runs_its_calls_alone():
    """Read 2026-11-20 from the stored sign-ins: a tool renews one only when its session runs out."""
    claims = base64.urlsafe_b64encode(json.dumps({"exp": 1_900_000_000}).encode()).decode().rstrip("=")
    codex = json.dumps({"auth.json": json.dumps({"tokens": {"access_token": f"h.{claims}.s"}})})

    assert cli.expires("codex_login", codex) == 1_900_000_000
    assert abs(cli.expires("claude_login", _signed_in(60)) - (time.time() + 60)) < 5
    assert cli.expires("claude_login", "{}") is None
    assert cli.expires("gemini_login", "{}") is None


def test_calls_on_one_sign_in_run_together_while_its_session_lasts_and_a_renewal_runs_alone():
    """Owner, 2026-11-20: cut the bench's hours, fairly. Calls on one sign-in once ran one at a time, because
    a renewal retires the copy every other call holds (one retired the local Claude sign-in, 2026-11-14).
    A tool renews only when the session runs out, so calls run together while it lasts; one that may renew
    runs alone, and the sign-in it renewed is the one kept and used after it.

    Together is seen, not timed: the stand-in holds each call until all that should run beside it are in
    it too, and calls made to wait their turn never are (one held alone gives up, and `most` says so). Only
    the renewal takes a moment, which is the calls behind it being given the time to start beside it, were
    they let."""
    saved, running, most, renewed_beside = [], [0], [0], []
    lock = threading.Lock()
    beside: list[threading.Barrier] = []

    class Tool(cli.CliClient):
        def _run(self, model, home, work, folder, prompt, **_call):
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            credentials = home / ".credentials.json"
            # As the real tool does: it renews the session it was given only if that has run out.
            if json.loads(credentials.read_text())["claudeAiOauth"]["expiresAt"] / 1000 < time.time():
                time.sleep(0.3)
                renewed_beside.append(running[0] - 1)
                credentials.write_text(json.loads(_signed_in(3600))[".credentials.json"])
            else:
                with contextlib.suppress(threading.BrokenBarrierError):
                    beside[-1].wait()
            with lock:
                running[0] -= 1
            return "ok"

    def together(client, calls):
        call = {"model": "", "system": "", "messages": [{"content": "hi"}], "max_tokens": 0}
        threads = [threading.Thread(target=client.complete_text, kwargs=call) for _ in range(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    fresh = Tool("claude_login", _signed_in(3600), saved.append)
    beside.append(threading.Barrier(3, timeout=5))
    together(fresh, 3)
    assert most[0] == 3 and saved == []

    most[0] = 0
    stale = Tool("claude_login", _signed_in(-60), saved.append)
    beside.append(threading.Barrier(2, timeout=5))
    together(stale, 3)
    # Renewed once, with nothing else running; then the other two ran together on the renewed sign-in.
    assert renewed_beside == [0] and len(saved) == 1 and most[0] == 2
    assert cli.expires("claude_login", saved[0]) > time.time() + 3000
