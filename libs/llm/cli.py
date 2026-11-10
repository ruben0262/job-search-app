"""Writing with a signed-in account: the account's own command-line tool, run once per call.

A sign-in (`libs.signin`) leaves the files that provider's tool writes when a person logs in: Claude
Code's, Codex's or Gemini CLI's. They are kept sealed (`libs.secrets`), as one JSON object of file
name to contents. A call unpacks them into a private folder that exists for that call alone, runs the
tool there with nothing else in its environment, and takes the answer. The tool renews a sign-in on its
own when it needs to; the files it leaves are sealed again (`on_refresh`), so the next call has them.

One docgen worker makes the calls, so two never renew one sign-in at the same moment. A second worker
would need a lock per account first: a renewed sign-in can retire the one another call is holding.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from libs.llm.client import TIMEOUT_SEC, LlmError

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


class CliClient:
    """`LlmClient` for a signed-in account: plain text only. Tools and JSON schemas are the help chat's,
    which runs on API keys (`libs.llm.providers.resolve`)."""

    def __init__(self, kind: str, blob: str, on_refresh: Callable[[str], object]):
        if kind not in TOOLS:
            raise ValueError(f"{kind!r} is not a signed-in kind")
        self._kind, self._blob, self._on_refresh = kind, blob, on_refresh

    def complete_text(self, *, model: str, system: str, messages: list[dict],
                      max_tokens: int, thinking_tokens: int | None = None) -> str:
        """One answer. `max_tokens` and `thinking_tokens` are the tool's own to decide."""
        del max_tokens, thinking_tokens
        prompt = "\n\n".join(str(message["content"]) for message in messages)
        with tempfile.TemporaryDirectory(prefix="ai-") as folder:
            home, work = Path(folder) / "home", Path(folder) / "work"
            home.mkdir(mode=0o700)
            work.mkdir(mode=0o700)
            prepare(self._kind, home)
            unpack(self._kind, self._blob, home)
            (Path(folder) / "system.md").write_text(system)
            try:
                return self._run(model, home, work, Path(folder), prompt)
            finally:
                renewed = snapshot(self._kind, home)
                if renewed and renewed != self._blob:
                    self._blob = renewed
                    self._on_refresh(renewed)

    def complete_json(self, **_kwargs) -> dict:
        raise LlmError("a signed-in account writes documents only")

    def complete_tools(self, **_kwargs) -> dict:
        raise LlmError("a signed-in account writes documents only")

    def _run(self, model: str, home: Path, work: Path, folder: Path, prompt: str) -> str:
        binary, system = TOOLS[self._kind].path(), str(folder / "system.md")
        env = environment(self._kind, home)
        answer = folder / "answer.txt"
        if self._kind == "claude_login":
            argv = [binary, "-p", "--output-format", "json", "--system-prompt-file", system, "--tools", "",
                    "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
            argv += ["--model", model] if model else []
            stdin = prompt
        elif self._kind == "codex_login":
            argv = [binary, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                    "--sandbox", "read-only", "--cd", str(work), "--output-last-message", str(answer)]
            argv += ["--model", model] if model else []
            argv += ["-"]
            # Codex keeps its own instructions; what this platform asks comes first in the prompt.
            stdin = f"{(folder / 'system.md').read_text()}\n\n---\n\n{prompt}"
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
        text = self._answer(done, answer)
        if not text.strip():
            raise LlmError(f"{TOOLS[self._kind].name} wrote nothing")
        return text

    def _answer(self, done: subprocess.CompletedProcess, answer: Path) -> str:
        """The answer, or why there is none. Claude and Gemini print their JSON even when they fail,
        with the reason inside it, so it is read before the exit code."""
        if self._kind == "codex_login":
            if done.returncode == 0 and answer.is_file():
                return answer.read_text()
            raise self._failed(done.stderr or done.stdout)
        try:
            reply = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise self._failed(done.stderr or done.stdout) from None
        if self._kind == "claude_login":
            if reply.get("is_error") or done.returncode:
                raise self._failed(str(reply.get("result") or done.stderr))
            return str(reply.get("result") or "")
        if reply.get("error") or done.returncode:
            raise self._failed(json.dumps(reply.get("error")) if reply.get("error") else done.stderr)
        return str(reply.get("response") or "")

    def _failed(self, said: str) -> LlmError:
        # Its own words, cut short: they say why (signed out, over a limit), and never hold the prompt,
        # which went in on stdin.
        said = (said or "").strip()[-300:]
        name = TOOLS[self._kind].name
        logger.warning("cli: %s could not answer: %s", name, said)
        return LlmError(f"{name} could not write this ({_why(said)})")


def _why(said: str) -> str:
    """The reason a person can act on, from what a tool printed."""
    lowered = said.lower()
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
