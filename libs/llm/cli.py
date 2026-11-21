"""Writing with a signed-in account: the account's own command-line tool, run once per call.

A sign-in (`libs.signin`) leaves the files that provider's tool writes when a person logs in: Claude
Code's, Codex's or Gemini CLI's. They are kept sealed (`libs.secrets`), as one JSON object of file
name to contents. A call unpacks them into a private folder that exists for that call alone, runs the
tool there with nothing else in its environment, and takes the answer. The tool renews a sign-in on its
own when it needs to; the files it leaves are sealed again (`on_refresh`), so the next call has them.

A tool renews a sign-in only when its session runs out (`expires`), and the renewal retires the copy every
other call is holding. So calls on one client run together while the session has time left, and a call that
may renew runs alone (`CliClient._turn`). That holds within one client: two clients, or two processes, on one
sign-in can still retire it, which is why one docgen worker makes the calls.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from libs.llm.client import TIMEOUT_SEC, LlmError, Reply, counted

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    """One provider's command-line tool: where its sign-in lives, how it signs in, how it answers."""

    name: str
    # The variable naming the binary, so a test or an image can point at its own.
    binary_env: str
    binary: str
    # The variable that moves the tool's own folder; None when it is the home folder's `.gemini`.
    home_env: str | None
    # What a sign-in leaves, relative to that folder: all a call needs, and all that is kept.
    files: tuple[str, ...]
    # How a sign-in starts, after the binary.
    signin: tuple[str, ...]

    def path(self) -> str:
        return os.environ.get(self.binary_env, self.binary)


TOOLS = {
    "claude_login": Tool("Claude", "CLAUDE_CLI", "claude", "CLAUDE_CONFIG_DIR",
                         (".credentials.json", ".claude.json"), ("auth", "login", "--claudeai")),
    "codex_login": Tool("ChatGPT", "CODEX_CLI", "codex", "CODEX_HOME", ("auth.json",),
                        ("login", "--device-auth")),
    "gemini_login": Tool("Google Gemini", "GEMINI_CLI", "gemini", None,
                         (".gemini/oauth_creds.json", ".gemini/google_accounts.json"), ()),
}

# What Gemini CLI is told to sign in with, and that it sends nothing about its use back.
_GEMINI_SETTINGS = {"security": {"auth": {"selectedType": "oauth-personal"}},
                    "privacy": {"usageStatisticsEnabled": False}}


def environment(kind: str, home: Path) -> dict[str, str]:
    """The tool's whole environment: its folder, and nothing inherited that could carry a key."""
    tool = TOOLS[kind]
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home),
           "LANG": "C.UTF-8", "NO_COLOR": "1", "TERM": "dumb",
           # The person opens the link in their own browser; none is opened where the tool runs.
           "BROWSER": "/bin/true",
           # Claude Code: no update checks, no telemetry, from a server that only runs it.
           "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    if tool.home_env:
        env[tool.home_env] = str(home)
    if kind == "gemini_login":
        env["NO_BROWSER"] = "true"
    return env


def prepare(kind: str, home: Path) -> None:
    """What the tool needs in its folder before it runs, besides the sign-in itself."""
    if kind == "gemini_login":
        (home / ".gemini").mkdir(mode=0o700, exist_ok=True)
        (home / ".gemini" / "settings.json").write_text(json.dumps(_GEMINI_SETTINGS))


def snapshot(kind: str, home: Path) -> str | None:
    """The sign-in the tool left in `home`, as the one string that is sealed; None if it left none."""
    files = {name: (home / name).read_text() for name in TOOLS[kind].files if (home / name).is_file()}
    return json.dumps(files, sort_keys=True) if TOOLS[kind].files[0] in files else None


def unpack(kind: str, blob: str, home: Path) -> None:
    """Put a sealed sign-in back where its tool looks for it, readable by this process alone."""
    for name, text in json.loads(blob).items():
        if name not in TOOLS[kind].files:
            continue  # only the files a sign-in is made of, wherever the blob came from
        target = home / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(0o600)


def label(kind: str, blob: str) -> str:
    """What Settings calls the account: its plan and its address, where the sign-in says them."""
    files = json.loads(blob)
    name, plan, email = TOOLS[kind].name, None, None
    try:
        if kind == "claude_login":
            plan = json.loads(files[".credentials.json"]).get("claudeAiOauth", {}).get("subscriptionType")
            email = json.loads(files.get(".claude.json", "{}")).get("oauthAccount", {}).get("emailAddress")
        elif kind == "codex_login":
            token = json.loads(files["auth.json"]).get("tokens", {}).get("id_token", "")
            claims = _jwt_claims(token)
            email = claims.get("email")
            plan = claims.get("https://api.openai.com/auth", {}).get("chatgpt_plan_type")
        elif kind == "gemini_login":
            email = json.loads(files.get(".gemini/google_accounts.json", "{}")).get("active")
    except (ValueError, KeyError, AttributeError):
        logger.warning("cli: the %s sign-in did not say who it is; named by its provider alone", kind)
    title = f"{name} {plan.title()}" if isinstance(plan, str) and plan else name
    return f"{title} · {email}" if isinstance(email, str) and email else title


# How hard Codex is told to think: not told, so a ChatGPT sign-in writes at Codex's own setting (none, on
# the model a Plus plan gets, 2026-11-15). Only the bench sets it (`scripts/cvlab/run.py --codex-effort`),
# to measure one before every ChatGPT user's documents change with it (owner, 2026-11-15).
CODEX_EFFORT: str | None = None
# Whether Codex writes from this platform's instructions alone. Asked plainly, it adds its own: a coding
# agent's prompt and its tools (shell, browser, images, other agents, web search) with their guidance, which a
# person's ChatGPT plan pays for on every call: "reply with one word" took 13,856 tokens in, and 4,842 lean
# (2026-11-19). A letter is written lean (`services.docgen.build.LEAN`, which sets `CliClient.lean` for the
# call); a CV is not until it is measured so, and only the bench turns this on for one
# (`scripts/cvlab/run.py --codex-lean`). Once CVs are measured, lean is the only path and the plain one goes.
CODEX_LEAN = False
# The features that bring Codex a tool or the instructions for one, all off when it is lean.
_CODEX_TOOLS = ("apps", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
                "browser_annotation_api", "computer_use", "image_generation", "in_app_browser", "multi_agent",
                "plugins", "remote_plugin", "plugin_discovery_prefetch", "shell_tool", "unified_exec",
                "unified_exec_tty", "code_mode_host", "view_image", "sleep_tool", "skill_search",
                "skill_mcp_dependency_install", "tool_suggest", "goals", "hooks", "collaboration_modes",
                "workspace_dependencies", "worktrees", "mentions_v2", "deferred_tool_world_state",
                "incremental_tools", "tool_call_mcp_elicitation", "tool_search_always_defer_mcp_tools",
                "write_stdin_approval", "guardian_approval", "shell_snapshot")
_CODEX_LEAN_SETTINGS = ('web_search="disabled"', "include_permissions_instructions=false",
                        "include_apps_instructions=false", "include_collaboration_mode_instructions=false",
                        *(f"features.{name}=false" for name in _CODEX_TOOLS))
_CODEX_LEAN_ARGS = tuple(arg for setting in _CODEX_LEAN_SETTINGS for arg in ("-c", setting))
# Whether Claude Code may search the web while it writes: off, so a sign-in writes from what it is given, as
# an API key does. Only the bench turns it on (`scripts/cvlab/run.py --research`), to measure the AI looking a
# company up itself against facts read by rules (owner, 2026-11-16). Search only: WebFetch would fetch pages
# from this server, where a posting's words could point it at a private address.
WEB_SEARCH = False


# How long a session must still have for a call to run beside others: longer than any call takes, and than
# the few minutes before the end at which a tool renews early. Below it, a call waits to run alone.
FRESH_FOR = 30 * 60


def expires(kind: str, blob: str) -> float | None:
    """When the session in a sign-in runs out, in seconds since the epoch; None where it does not say so, and
    then every call runs alone. Read 2026-11-20: Claude's lasts about five hours, Codex's ten days, and
    neither was renewed in the hundreds of calls between."""
    try:
        files = json.loads(blob)
        if kind == "claude_login":
            return json.loads(files[".credentials.json"])["claudeAiOauth"]["expiresAt"] / 1000
        if kind == "codex_login":
            return float(_jwt_claims(json.loads(files["auth.json"])["tokens"]["access_token"])["exp"])
    except (KeyError, TypeError, ValueError):
        return None
    return None


class CliClient:
    """`LlmClient` for a signed-in account: plain text only. Tools and JSON schemas are the help chat's,
    which runs on API keys (`libs.llm.providers.resolve`)."""

    def __init__(self, kind: str, blob: str, on_refresh: Callable[[str], object]):
        if kind not in TOOLS:
            raise ValueError(f"{kind!r} is not a signed-in kind")
        self._kind, self._blob, self._on_refresh = kind, blob, on_refresh
        # A Codex call's own settings, where they differ from the module's: the bench's reviewer runs lean
        # and thinks hard while the letters it reads were written otherwise. None follows the module.
        self.lean: bool | None = None
        self.codex_effort: str | None = None
        # The calls running now, and whether one of them is running alone because it may renew.
        self._turns, self._running, self._alone = threading.Condition(), 0, False

    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, effort: str | None = None, lean: bool | None = None) -> str:
        """One answer. `max_tokens` is the tool's own to decide; `effort` is passed to Claude Code, so a
        sign-in thinks as hard as an API key does. `lean`, for this call alone (`CODEX_LEAN`)."""
        del max_tokens
        prompt = "\n\n".join(str(message["content"]) for message in messages)
        with self._turn(), tempfile.TemporaryDirectory(prefix="ai-") as folder:
            home, work = Path(folder) / "home", Path(folder) / "work"
            home.mkdir(mode=0o700)
            work.mkdir(mode=0o700)
            prepare(self._kind, home)
            started = self._blob
            unpack(self._kind, started, home)
            (Path(folder) / "system.md").write_text(system)
            try:
                return self._run(model, home, work, Path(folder), prompt, effort=effort, lean=lean)
            finally:
                # Against the copy this call started from: one that renewed nothing never writes it back
                # over a newer one.
                renewed = snapshot(self._kind, home)
                if renewed and renewed != started:
                    self._blob = renewed
                    self._on_refresh(renewed)

    @contextmanager
    def _turn(self) -> Iterator[None]:
        """A call's turn: beside the others while the session has `FRESH_FOR` left, else alone, once the
        calls before it are done, so that a renewal retires no copy another call is holding."""
        with self._turns:
            while True:
                fresh = (expires(self._kind, self._blob) or 0) - time.time() > FRESH_FOR
                if not self._alone and (fresh or not self._running):
                    break
                self._turns.wait()
            self._alone, self._running = not fresh, self._running + 1
        try:
            yield
        finally:
            with self._turns:
                self._alone, self._running = False, self._running - 1
                self._turns.notify_all()

    def complete_tools(self, **_kwargs) -> dict:
        raise LlmError("a signed-in account writes documents only")

    def _run(self, model: str, home: Path, work: Path, folder: Path, prompt: str, *,
             effort: str | None = None, lean: bool | None = None) -> str:
        binary, system = TOOLS[self._kind].path(), str(folder / "system.md")
        env = environment(self._kind, home)
        answer = folder / "answer.txt"
        if self._kind == "claude_login":
            tools = "WebSearch" if WEB_SEARCH else ""
            argv = [binary, "-p", "--output-format", "json", "--system-prompt-file", system, "--tools", tools,
                    "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
            argv += ["--allowedTools", "WebSearch"] if WEB_SEARCH else []
            argv += ["--model", model] if model else []
            argv += ["--effort", effort] if effort else []
            stdin = prompt
        elif self._kind == "codex_login":
            # `--json`: what the call used comes with the answer, as Claude's and Gemini's do.
            argv = [binary, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                    "--sandbox", "read-only", "--cd", str(work), "--output-last-message", str(answer)]
            argv += ["--model", model] if model else []
            effort_told = self.codex_effort if self.codex_effort is not None else CODEX_EFFORT
            argv += ["-c", f"model_reasoning_effort={effort_told}"] if effort_told else []
            if next(each for each in (lean, self.lean, CODEX_LEAN) if each is not None):
                # This platform's instructions in place of Codex's own, and none of its tools.
                argv += ["-c", f"model_instructions_file={json.dumps(system)}", *_CODEX_LEAN_ARGS]
                stdin = prompt
            else:
                # Codex keeps its own instructions; what this platform asks comes first in the prompt.
                stdin = f"{(folder / 'system.md').read_text()}\n\n---\n\n{prompt}"
            argv += ["-"]
        else:
            # Trusted, or Gemini CLI quietly drops the read-only mode in a folder it has not seen.
            argv = [binary, "--output-format", "json", "--approval-mode", "plan", "--skip-trust",
                    "--prompt", "Answer the request above."]
            argv += ["--model", model] if model else []
            env["GEMINI_SYSTEM_MD"] = system
            stdin = prompt
        try:
            done = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env,  # noqa: S603
                                  cwd=work, timeout=TIMEOUT_SEC, check=False)
        except FileNotFoundError as exc:
            logger.error("cli: %s is not installed here (%s)", TOOLS[self._kind].name, binary)
            raise LlmError(f"{TOOLS[self._kind].name} cannot be reached from here") from exc
        except subprocess.TimeoutExpired as exc:
            logger.error("cli: %s gave no answer within %ds", TOOLS[self._kind].name, TIMEOUT_SEC)
            raise LlmError(f"{TOOLS[self._kind].name} took too long to answer") from exc
        text = self._answer(done, answer, model)
        if not text.strip():
            raise LlmError(f"{TOOLS[self._kind].name} wrote nothing")
        return text

    def _answer(self, done: subprocess.CompletedProcess, answer: Path, model: str = "") -> Reply:
        """The answer, with what the call used where the tool says, or why there is none. Claude and Gemini
        print their JSON even when they fail, with the reason inside it, so it is read before the exit
        code. Codex writes its last message to a file and its events as lines of JSON, the last turn's use
        among them; they name no model, so it is the one asked for, and none when the plan chose."""
        if self._kind == "codex_login":
            events = _events(done.stdout)
            if done.returncode == 0 and answer.is_file():
                done_turns = [event.get("usage") for event in events if event.get("type") == "turn.completed"]
                used = done_turns[-1] if done_turns and isinstance(done_turns[-1], dict) else {}
                return Reply(answer.read_text(), counted(used.get("input_tokens"), used.get("output_tokens")),
                             model or None)
            said = [str(event.get("message") or (event.get("error") or {}).get("message") or "")
                    for event in events if event.get("type") in ("error", "turn.failed")]
            raise self._failed("\n".join(each for each in said if each) or done.stderr or done.stdout)
        try:
            reply = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise self._failed(done.stderr or done.stdout) from None
        if self._kind == "claude_login":
            if reply.get("is_error") or done.returncode:
                raise self._failed(str(reply.get("result") or done.stderr))
            used = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
            # What went in is the prompt however it was billed: new, written to the cache or read from it.
            sent = [used.get(name) for name in ("input_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens")]
            return Reply(str(reply.get("result") or ""),
                         counted(sum(each or 0 for each in sent) if isinstance(sent[0], int) else None,
                                 used.get("output_tokens")), _named(reply.get("modelUsage")))
        if reply.get("error") or done.returncode:
            raise self._failed(json.dumps(reply.get("error")) if reply.get("error") else done.stderr)
        # Gemini CLI counts per model it used: `stats.models.<model>.tokens`.
        models = (reply.get("stats") or {}).get("models")
        tokens = [each.get("tokens") or {} for each in (models or {}).values() if isinstance(each, dict)]
        return Reply(str(reply.get("response") or ""), counted(
            sum(each.get("prompt") or 0 for each in tokens),
            sum((each.get("candidates") or 0) + (each.get("thoughts") or 0) for each in tokens))
            if tokens else None, _named(models))

    def _failed(self, said: str) -> LlmError:
        # Its own words, cut short: they say why (signed out, over a limit), and never hold the prompt,
        # which went in on stdin.
        # The line that says why, where the tool prints one: Gemini's is the first of a long trace, and
        # the end of what Codex prints can be the person's own prompt echoed back.
        reason = re.search(r"(?im)^\s*(?:error|fatal)\b.*$", said or "")
        said = reason.group(0).strip()[:300] if reason else (said or "").strip()[-300:]
        name = TOOLS[self._kind].name
        logger.warning("cli: %s could not answer: %s", name, said)
        return LlmError(f"{name} could not write this ({_why(said)})")


def _events(printed: str | None) -> list[dict]:
    """Codex's events, one JSON object a line; a line that is not one is passed over."""
    found = []
    for line in (printed or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def _named(models: object) -> str | None:
    """The model a tool says it used, from its count per model. More than one, when it used a small
    model beside the one that wrote: all of them, the name that did most of the writing first."""
    if not isinstance(models, dict) or not models:
        return None

    def written(name: str) -> int:
        used = models[name] if isinstance(models[name], dict) else {}
        tokens = used.get("tokens") if isinstance(used.get("tokens"), dict) else used
        return int(tokens.get("outputTokens") or tokens.get("candidates") or 0)

    return ", ".join(sorted(models, key=written, reverse=True))


def _why(said: str) -> str:
    """The reason a person can act on, from what a tool printed."""
    lowered = said.lower()
    if "no longer supported" in lowered or "ineligibletier" in lowered:
        # Before the sign-in words: signing in again cannot help.
        return "its provider no longer takes this kind of sign-in: link an API key for it in Settings"
    if any(word in lowered for word in ("log in", "logged in", "login", "sign in", "unauthorized", "401",
                                        "expired", "credentials", "authenticat", "authoriz")):
        return "the sign-in has expired: sign in again in Settings"
    if any(word in lowered for word in ("limit", "quota", "429", "rate")):
        return "the account has reached its usage limit for now"
    return "it gave no reason this site understands"


def _jwt_claims(token: str) -> dict:
    """A JWT's claims, read only to name the account. Not verified: nothing is decided on them."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
