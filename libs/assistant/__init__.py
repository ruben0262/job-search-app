"""The help chat: a reply written by an AI on the key that pays, streamed into its row.

Its design is the owner's project-planner portal, ported as ideas rather than code:

- **The reply row is the run's state.** The reply is written into its `setup_messages` row as it
  arrives, at most `TICK` apart, and the page's event stream reads that row. So it does not matter
  which of the API's workers writes a reply or which one the page reaches, and a page that leaves
  and comes back finds the answer there.
- **Restart-proof without a journal.** A reply still `pending` or `streaming` when the API starts is
  written again from the start (`resume`): the rows are the journal. Whether a live process is
  writing one is a lease on its row, taken in one atomic write and renewed at every save, so four
  workers starting at once write it once, and a process that dies lets go when the lease runs out.
- **A guard on every tool call** (`Tools.call`): a lookup runs; a change to the account becomes a
  button under the reply. Nothing here can make the change — that code is in the API, where only the
  person's tap reaches it.
- **One runner interface**, `complete_tools`: each provider's in `libs.llm`, and the scripted
  model's in the tests.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.database import Database

from libs import limits, settings, suggestions
from libs.contracts.config import location_search_name, migrate_config
from libs.db import database, get, now
from libs.db.models import SetupMessage, User
from libs.documents.read import LANGUAGE_NAMES
from libs.legal import has_consented
from libs.llm import PLATFORM, LlmError, ai_requests_today, paying_client, platform_left, resolve
from libs.llm.client import TIMEOUT_SEC
from libs.matching import green_words, matching_jobs, norm, pattern, score
from libs.prompts import load
from libs.secrets import SecretStore
from libs.tenant.repository import TenantRepository

logger = logging.getLogger(__name__)

# The help chat lives in the setup chat's tables, its rows told apart by `agent`.
SUPPORT = "support"
RUNNING = ("pending", "streaming")
# How much of the conversation is kept in view: what the page shows, and what the model is given.
HISTORY = 20
# The most model calls one reply may make. A reply is one request against the allowance however many
# lookups it takes, so this is what bounds what one costs.
ROUNDS = 4
MAX_TOKENS = 1_024
# How far behind the page may be, in seconds: the longest the writer waits between saves, and the
# stream between reads.
TICK = 0.1
# How long a reply's writer holds it between saves. Twice the longest a live writer can be silent —
# one model call — so only a writer that has died loses it, and a restart's replies resume after it.
LEASE = timedelta(seconds=TIMEOUT_SEC * 2)
PROVIDER_NAMES = {"anthropic": "Claude", "gemini": "Gemini", "openai": "OpenAI"}


# ── who pays ───────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AiStatus:
    # "own" (their own key), "platform" (the daily allowance) or None (nobody can pay now).
    payer: str | None
    provider: str | None
    # Requests left today; None is unlimited, on their own key.
    left: int | None
    per_day: int
    # Why nobody can pay, and what to do about it, in their words.
    why_not: str | None


def ai_status(db: Database, user: User) -> AiStatus:
    """Who would pay for one more answer, and what is left: `libs.llm.resolve`'s answer, said."""
    payer = resolve(db, user)
    allowance = limits.resolve(user, "ai_platform_requests_per_day")
    per_day = 0 if allowance is None else allowance
    if payer is not None:
        own = payer.kind != PLATFORM
        return AiStatus("own" if own else "platform", PROVIDER_NAMES[payer.provider],
                        None if own else platform_left(db, user), per_day, None)
    used_up = per_day and ai_requests_today(db, user.id) >= per_day
    return AiStatus(None, None, 0, per_day, (
        f"You have used today's {per_day} AI requests. They come back at midnight. To ask more "
        "now, add your own Claude, Gemini or OpenAI key in Settings." if used_up else
        "This site cannot pay for AI answers on your account. Add your own Claude, Gemini or "
        "OpenAI key in Settings, and you can ask as much as you like."))


# ── what the model may use ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Tool:
    description: str
    params: dict
    # What the person reads under the reply.
    label: Callable[[dict], str]
    # A lookup: its answer. A change: why it cannot be offered, or None to offer its button.
    run: Callable[[Database, User, dict], str | None]
    change: bool = False
    # Reads what their documents say, so it is offered only with their processing consent.
    documents: bool = False


def _config(db: Database, user: User) -> dict:
    stored = TenantRepository(db).get_config(user.id)
    return migrate_config(stored) if stored else {"sections": []}


def _red(config: dict) -> list[str]:
    return sorted({norm(word) for section in config["sections"]
                   for word in section.get("exclude_keywords") or [] if word.strip()})


def _words(db: Database, user: User, _args: dict) -> str:
    config = _config(db, user)
    places = {location_search_name(place) for section in config["sections"]
              for place in section.get("locations") or []}
    ways = {(section.get("remote_policy") or "").replace("_", " ") for section in config["sections"]}
    return "\n".join([
        f"Green words, which find jobs: {', '.join(green_words(config)) or 'none yet'}",
        f"Red words, which hide jobs: {', '.join(_red(config)) or 'none'}",
        f"Places: {', '.join(sorted(places)) or 'anywhere'}",
        "Languages: " + (", ".join(LANGUAGE_NAMES.get(code, (code,))[0]
                                   for code in config.get("languages") or []) or "any"),
        f"Way of working: {', '.join(sorted(ways - {''})) or 'any'}"])


def _why_job(db: Database, user: User, args: dict) -> str:
    wanted = norm(str(args.get("search") or "")).split()
    if not wanted:
        return "Ask them which job: a few words from its title or company."
    found = [match for match in matching_jobs(db, user)
             if all(word in norm(f"{match.job.title} {match.job.company or ''}") for word in wanted)]
    if not found:
        return (f"No job on their list has “{' '.join(wanted)}” in its title or company. A job is "
                "left out when a red word is in its title or company, when it is in a place, "
                "language or way of working they did not pick, or when it is too old.")
    in_title = pattern(green_words(_config(db, user)))
    lines = []
    for match in found[:3]:
        titled = set(score(in_title, match.job.title, None)[1])
        lines.append(f"“{match.job.title}” at {match.job.company or 'a company with no name'}: found by "
                     + ", ".join(f"{word} (in the {'title' if word in titled else 'text'})"
                                 for word in match.words))
    return "\n".join(lines)


def _ai_left(db: Database, user: User, _args: dict) -> str:
    status = ai_status(db, user)
    if status.payer == "own":
        return f"Unlimited: they use their own {status.provider} key."
    if status.payer is None:
        return str(status.why_not)
    return (f"{status.left} of their {status.per_day} AI requests are left today. The count "
            "starts again at midnight.")


def _suggested(db: Database, user: User, _args: dict) -> str:
    found = suggestions.current(db, user.id)
    return "\n".join(f"{kind} “{each['term']}”: {each['reason']}" for kind in ("green", "red")
                     for each in found.get(kind) or []) or "There are no words to suggest right now."


def _can_add(db: Database, user: User, args: dict) -> str | None:
    word, kind, config = norm(str(args.get("word") or "")), args.get("kind"), _config(db, user)
    if kind not in ("green", "red") or not 2 <= len(word) <= 40:
        return "a word is 2 to 40 letters, and green or red"
    if word in (green_words(config) if kind == "green" else _red(config)):
        return f"“{word}” is already one of their {kind} words"
    return None


def _can_remove(db: Database, user: User, args: dict) -> str | None:
    word, config = norm(str(args.get("word") or "")), _config(db, user)
    if word not in green_words(config) + _red(config):
        return f"“{word}” is not one of their words"
    if green_words(config) == [word]:
        return f"“{word}” is their only green word, and a search needs one"
    return None


def _can_stop(db: Database, user: User, args: dict) -> str | None:
    found = suggestions.current(db, user.id)
    word = norm(str(args.get("word") or ""))
    if word not in {each["term"] for kind in ("green", "red") for each in found.get(kind) or []}:
        return f"“{word}” is not one of the words we suggest"
    return None


_WORD = {"type": "string", "description": "One word or a short phrase."}
TOOLS: dict[str, Tool] = {
    "my_words": Tool("Their green and red words, places, languages and way of working.", {},
                     lambda _args: "Looked at your words", _words),
    "why_job": Tool(
        "Find jobs on their list by words from the job's title or company, and say which of their "
        "green words found each one.",
        {"search": {"type": "string", "description": "Words from the job's title or company."}},
        lambda args: f"Looked for “{args.get('search', '')}” in your jobs", _why_job),
    "ai_left": Tool("How many AI requests they have left today.", {},
                    lambda _args: "Checked your AI requests for today", _ai_left),
    "suggested_words": Tool("The words we suggest they add, worked out from their documents, each "
                            "with its reason.", {},
                            lambda _args: "Looked at the words we suggest", _suggested, documents=True),
    "add_word": Tool(
        "Offer a button that adds a word. green: show jobs with it. red: hide jobs with it.",
        {"word": _WORD, "kind": {"type": "string", "enum": ["green", "red"]}},
        lambda args: (f"Add “{norm(str(args.get('word') or ''))}” to the words that "
                      f"{'hide' if args.get('kind') == 'red' else 'find'} jobs"),
        _can_add, change=True),
    "remove_word": Tool("Offer a button that removes one of their words.", {"word": _WORD},
                        lambda args: f"Remove “{norm(str(args.get('word') or ''))}” from your words",
                        _can_remove, change=True),
    "stop_suggesting": Tool("Offer a button so a word we suggest is never suggested again.",
                            {"word": _WORD},
                            lambda args: f"Stop suggesting “{norm(str(args.get('word') or ''))}”",
                            _can_stop, change=True, documents=True),
}


class Tools:
    """What the model may use in one reply, and the guard every call it makes goes through."""

    def __init__(self, db: Database, user: User, reply: _Reply):
        consented = has_consented(db, user.id)
        self.db, self.user, self.reply = db, user, reply
        self.offered = {name: tool for name, tool in TOOLS.items() if consented or not tool.documents}

    def schemas(self) -> list[dict]:
        return [{"name": name, "description": tool.description,
                 "input_schema": {"type": "object", "properties": tool.params,
                                  "required": list(tool.params)}}
                for name, tool in self.offered.items()]

    def call(self, use: dict) -> dict:
        """The guard: a lookup runs, a change becomes a button, and anything else is refused."""
        tool, args = self.offered.get(use["name"]), use.get("input") or {}
        if tool is None:
            answer, step = f"There is no {use['name']} here.", {"label": "Tried something it may not do",
                                                               "error": True}
        elif not tool.change:
            try:
                answer, step = str(tool.run(self.db, self.user, args)), {"label": tool.label(args)}
            except Exception:  # one lookup failing must not cost the person the reply
                logger.warning("support: lookup %s failed for user %s", use["name"], self.user.id,
                               exc_info=True)
                answer, step = "That lookup failed.", {"label": tool.label(args), "error": True}
            step["output"] = answer
        elif refused := tool.run(self.db, self.user, args):
            answer, step = f"Not offered: {refused}.", {"label": tool.label(args), "output": refused,
                                                         "error": True}
        else:
            step = {"label": tool.label(args), "state": "proposed", "tool": use["name"],
                    "args": {**args, "word": norm(str(args.get("word")))}}
            if any(earlier.get("label") == step["label"] for earlier in self.reply.steps):
                return _result(use, "That button is already under your answer.")
            answer = (f"A button “{step['label']}” is now under your answer. Nothing changes until "
                      "they tap it.")
        self.reply.step(step)
        return _result(use, answer, error=bool(step.get("error")))


def _result(use: dict, answer: str, *, error: bool = False) -> dict:
    return {"type": "tool_result", "tool_use_id": use["id"], "content": answer,
            **({"is_error": True} if error else {})}


# ── writing one reply ──────────────────────────────────────────────────────────────────────────

class _Reply:
    """The reply as it is written: saved at most `TICK` apart, and at every step."""

    def __init__(self, db: Database, row: SetupMessage):
        self.db, self.row = db, row
        self.text, self.steps, self.saved, self.first, self.gap = "", [], 0.0, None, False
        row.status, row.error = "streaming", None
        self.save()

    def write(self, chunk: str) -> None:
        # The first words are saved at once: how soon they show is what the person feels.
        first, self.first = self.first is None, self.first or time.monotonic()
        self.text += ("\n\n" if self.gap and self.text else "") + chunk
        self.gap = False
        if first or time.monotonic() - self.saved >= TICK:
            self.save()

    def step(self, step: dict) -> None:
        self.steps.append(step)
        self.save()

    def save(self) -> None:
        """Write what there is, and renew the lease — or give it up, once the reply has ended."""
        self.row.content = self.text
        self.row.tool_calls = {**(self.row.tool_calls or {}), "steps": self.steps}
        self.db[SetupMessage.COLLECTION].update_one({"_id": self.row.id}, {"$set": {
            "content": self.row.content, "tool_calls": self.row.tool_calls, "status": self.row.status,
            "error": self.row.error,
            "lease_until": now() + LEASE if self.row.status in RUNNING else None}})
        self.saved = time.monotonic()


def start(reply_id: int) -> threading.Thread:
    """Write this reply on a thread of its own, so leaving the page cannot stop it."""
    thread = threading.Thread(target=_in_background, args=(reply_id,), daemon=True,
                              name=f"support-{reply_id}")
    thread.start()
    return thread


def _in_background(reply_id: int) -> None:
    try:
        run(database(), reply_id)
    except Exception:  # a thread's exception otherwise goes to stderr and is lost
        logger.exception("support: reply %s could not be written; it is tried again when the page "
                         "or the API next asks", reply_id)


def run(db: Database, reply_id: int) -> None:
    """Write one reply, unless it is finished or a live process holds its lease."""
    claimed = SetupMessage.from_doc(db[SetupMessage.COLLECTION].find_one_and_update(
        {"_id": reply_id, "agent": SUPPORT, "status": {"$in": list(RUNNING)},
         "$or": [{"lease_until": None}, {"lease_until": {"$lt": now()}}]},
        {"$set": {"lease_until": now() + LEASE}}, return_document=ReturnDocument.AFTER))
    if claimed is not None:
        _write(db, claimed)


def resume() -> int:
    """Start every reply the API's last stop cut off: the rows are the journal."""
    try:
        cut_off = database()[SetupMessage.COLLECTION].distinct(
            "_id", {"agent": SUPPORT, "status": {"$in": list(RUNNING)}})
    except Exception as exc:  # noqa: BLE001 - the API starts whether or not this can look
        logger.warning("support: could not look for replies a restart cut off: %s", exc)
        return 0
    for reply_id in cut_off:
        start(reply_id)
    if cut_off:
        logger.info("support: %d unfinished reply(ies) found at start-up, started again unless a "
                    "live worker holds them", len(cut_off))
    return len(cut_off)


def _write(db: Database, row: SetupMessage) -> None:
    started, user = time.monotonic(), get(db, User, row.user_id)
    payer = (row.tool_calls or {}).get("payer") or ""
    reply = _Reply(db, row)
    status, error = "done", None
    try:
        messages, tools = _transcript(db, row), Tools(db, user, reply)
        with paying_client(SecretStore(db), user.id, payer) as paid:
            if paid is None:
                raise LlmError("the key that was to pay for this answer has been removed")
            client, provider = paid
            model = settings.document_model(db, provider)
            for _ in range(ROUNDS):
                answer = client.complete_tools(model=model, system=load("support.md"),
                                               messages=messages, tools=tools.schemas(),
                                               max_tokens=MAX_TOKENS, on_text=reply.write)
                if not answer["tool_uses"]:
                    break
                messages += [{"role": "assistant", "content": answer["content"]},
                             {"role": "user", "content": [tools.call(use) for use in answer["tool_uses"]]}]
                reply.gap = True
            else:
                reply.write("I could not finish looking that up. Please ask again in fewer words.")
    except Exception as exc:  # a reply must end, whatever broke: somebody is waiting on it
        status = "failed"
        error = (f"The AI could not answer: {exc}. If this keeps happening, check your AI key in "
                 "Settings." if isinstance(exc, LlmError) else
                 "The AI could not answer just now. Please try again in a minute.")
        logger.error("support: reply %s for user %s failed: %s", row.id, user.id, exc,
                     exc_info=not isinstance(exc, LlmError))
    row.status, row.error = status, error
    reply.save()
    logger.info("support: reply %s for user %s %s in %.1fs, first text after %s, %d step(s), paid by %s",
                row.id, user.id, status, time.monotonic() - started,
                f"{reply.first - started:.2f}s" if reply.first else "none", len(reply.steps), payer)


def _transcript(db: Database, row: SetupMessage) -> list[dict]:
    """The conversation up to this reply, as text: what was said, and what came of each button."""
    earlier = [SetupMessage.from_doc(doc) for doc in db[SetupMessage.COLLECTION].find(
        {"user_id": row.user_id, "agent": SUPPORT, "_id": {"$lt": row.id}},
        sort=[("_id", -1)], limit=HISTORY)]
    messages: list[dict] = []
    for message in reversed(earlier):
        said = message.content + "".join(
            f"\n\n(Button “{step['label']}”: {_TAPPED[step['state']]})"
            for step in steps_of(message) if step.get("state"))
        if not said.strip():
            continue
        if messages and messages[-1]["role"] == message.role:
            messages[-1]["content"] += f"\n\n{said}"
        else:
            messages.append({"role": message.role, "content": said})
    return messages[next((n for n, m in enumerate(messages) if m["role"] == "user"), len(messages)):]


_TAPPED = {"proposed": "not tapped", "done": "tapped, and done", "failed": "tapped, and it did not work"}


def steps_of(message: SetupMessage) -> list[dict]:
    """What a help-chat reply used and offered, in order."""
    return (message.tool_calls or {}).get("steps") or []
