"""How well a job fits a person, 1 to 10, from their documents alone. Rules and a fitted calibration; no AI
call (owner, 2026-11-15 and 2026-11-16; CLAUDE.md §0). Not the learned model under the tag
`model-on-postgres` (CLAUDE.md §3): nothing here learns per person.

**What it compares.** A job's title, read for the job it names, against the words of a person's own roles,
the phrases of their CVs that name a job (`roles`); and the job's text, less what it says about the company
and the package, against everything they wrote, as two vectors of words weighted by how rare each is in the
corpus, in its own language. A title that matches while the text does not counts less. A job a level or two
above them, or one that needs a language they do not speak, counts less. Those four signals, weighted and
mapped to 1 to 10 by `model.json`, which `scripts/cvlab/fit/model.py` fits to readings of real postings
against made-up people of every trade. A job they cannot take, one requiring a credential they hold no part
of or open only to its own staff or to students, shows at most `CEILING` (`requirements`). On 12 people
nothing was fitted to, it orders a list better than the Jobs page's own order for 10 of 12 (CHANGELOG
2026-11-16, evening); on 24, the number is 0.12 nearer the readers' than without the last two (2026-11-16,
night).

**Where it runs.** A job's features are worked out once, with its text (`job`); a person's profile once after
their documents change (`profile`); the score is the database comparing the two (`expression`), so the site
runs what the bench measures. A page's cards are scored with the page (`scored`). A filter over a whole list
reads the scores kept for their profile (`fit_scores`, `kept`), and works out the rest there and then
(`at_least`), so a kept score only ever makes it quicker; `fill` keeps them after a page that needed them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import pathlib
import re
import uuid as _uuid
import zlib
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from functools import cache
from threading import Lock
from time import monotonic

from pymongo import UpdateOne
from pymongo.database import Database

from libs.corpus.langfilter import detect_language
from libs.db import now
from libs.db.models import CorpusJob, FitScore, User
from libs.documents.read import fold, read
from libs.fit import requirements
from libs.fit.roles import role_phrases
from libs.fit.text import (
    IDENTITY,
    MIDDLE,
    NEGATION,
    NOISE,
    core_title,
    languages_required,
    languages_spoken,
    title_level,
    words,
    working_text,
)

logger = logging.getLogger(__name__)

# What a job's features and a person's profile are made by: a change to how either is read moves it, and every
# posting is read again (`catch_up`) and every profile on its next use (`profile`). 2: roles read from role
# phrases, and what a posting requires (2026-11-16, night). 3: a pharmacist's registration apart from a
# technician's, and the credentials the third held-out set found missing (2026-11-17).
VERSION = 3
# Postings written per round trip by `catch_up`.
_BATCH = 500
# The positions a word can take in the arrays a request sends: 8,192 measured the same as exact words (0.673
# against 0.677 on the evaluation set) and 2,048 lost 0.04 (2026-11-16).
POSITIONS = 8192
# How many of a job's words it keeps: a hundred measured the same as all of them, forty lost 0.03.
WORDS = 100
# Rarity counts up to here: a word in fewer than about 3% of postings weighs what any rarer word does.
# Measured no worse than 6, and it means only common words need a count (`common.json`).
CAP = 3.5
# A title word they have but not in a role of theirs counts this much of one they have held; a word they said
# they have not got (About you: "I have never used Spark") counts against them, this much.
ELSEWHERE, NEGATED = 0.4, 0.5
# A title that matches counts fully only when their texts are at least this alike.
GATE = 0.02
# The most a job scores that they cannot take: one requiring a credential they hold no part of, one open only
# to its own staff, or only to students when they are not one (`requirements`). The rubric's own rule for a
# blocker; measured flat from 3.5 to 4.5 (2026-11-16, night).
CEILING = 4.0
# The documents read as what they have done; the rest is read as what they say about it.
_CV_KINDS = ("cv", "linkedin")
# A language with fewer postings than this is counted with every language together.
_OWN_COUNT = 500
_HERE = pathlib.Path(__file__).parent


@cache
def _common() -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    data = json.loads((_HERE / "common.json").read_text(encoding="utf-8"))
    return data["n"], data["df"]


def rarity(word: str, language: str | None) -> float:
    """How rare a word is among postings in their language, capped at `CAP`: `common.json` holds only the
    words under the cap, so a word not in it is a rare one."""
    counts, tables = _common()
    table = language if language in tables and counts.get(language, 0) >= _OWN_COUNT else "*"
    found = tables[table].get(word)
    return CAP if found is None else min(CAP, math.log((counts[table] + 1) / (found + 1)))


def position(word: str) -> int:
    return zlib.crc32(word.encode()) % POSITIONS


# ── a job ──────────────────────────────────────────────────────────────────────────────────────

def job(title: str, description: str | None, language: str | None) -> dict:
    """What the score reads of a posting, stored on it with its text. Positions and weights in arrays of their
    own, so the database reads one weight by index rather than one word by name."""
    head = [word for word in dict.fromkeys(words(core_title(title))) if word not in NOISE]
    counts = Counter(words(working_text(description)))
    for word in words(title):
        counts[word] += 3
    vector = {word: (1 + math.log(count)) * rarity(word, language) for word, count in counts.items()
              if word not in NOISE}
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    top = sorted(vector.items(), key=lambda item: (-item[1], item[0]))[:WORDS]
    total = sum(rarity(word, language) for word in head) or 1.0
    needs = set(languages_required(description))
    return {"th": [position(word) for word in head],
            "tw": [round(rarity(word, language) / total, 4) for word in head],
            "vh": [position(word) for word, _ in top], "vw": [round(value / norm, 4) for _, value in top],
            "level": title_level(title), "needs": sorted(needs | ({language} if language else set())),
            **requirements.posting(title, description)}


def catch_up(db: Database, *, deadline: float) -> int:
    """Features for the postings whose text was stored without them, or by an older `VERSION`, newest first,
    until `deadline` (`time.monotonic`): a column nothing writes is the feature missing (CLAUDE.md §6). Run
    after each harvest pass, beside `libs.corpus.terms.catch_up`."""
    corpus, given, asked = db[CorpusJob.COLLECTION], 0, Counter()
    while monotonic() < deadline:
        rows = list(corpus.find({"described_at": {"$ne": None}, "fit_version": {"$ne": VERSION}},
                                {"title": 1, "description": 1, "language": 1},
                                sort=[("first_seen_at", -1)]).limit(_BATCH))
        if not rows:
            break
        made = {row["_id"]: job(row["title"], row.get("description"), row.get("language")) for row in rows}
        corpus.bulk_write([UpdateOne({"_id": key}, {"$set": {"fit": features, "fit_version": VERSION}})
                           for key, features in made.items()], ordered=False)
        given += len(rows)
        asked.update(name for features in made.values() for group in features["req"] for name in group)
    if given:
        # Which credentials the postings require, so one the list never finds shows by its absence: a
        # credential it does not know never caps a score (CLAUDE.md §5).
        logger.info("fit: %d posting(s) given their features; credentials required %s", given,
                    dict(asked.most_common()))
    return given


# ── a person ───────────────────────────────────────────────────────────────────────────────────

def person(documents: Mapping[str, Sequence[str]], name: str | None = None) -> dict | None:
    """What the score reads of a person, from every document they have switched on, by kind. None with no
    text.

    `title`: the weight each of their words carries when a job's title has it, 1 for the words of their own
    roles. `vector`: their words weighted by count and rarity, as a unit vector. `level`: 0 (a trainee) to 4
    (a head), from their years and the titles they held. `spoken`: the languages their documents are in or
    name."""
    texts = [text for kind in documents for text in (documents[kind] or ()) if text]
    if not texts:
        return None
    facts = read(documents)
    identity = set(words(name)) | set(words(facts.name))
    read_ = _Read(identity, fold(facts.name) if facts.name else None)
    for kind, kept in documents.items():
        for text in kept or ():
            read_.document(kind, text)
    counts = read_.counts
    spoken = set(facts.languages) | languages_spoken(texts) | read_.spoken
    for held in facts.jobs:
        for word in set(words(held.title)) - NOISE - identity:
            counts[word] += 2
    for skill in facts.skills:
        for word in set(words(skill)) - identity:
            counts[word] += 1
    # Their roles: the phrases of their CVs that name a job (`roles.role_phrases`), not a CV's first lines,
    # which gave "Leeds" and an address as roles (2026-11-16, night).
    roles: set[str] = set()
    for kind in _CV_KINDS:
        for text in documents.get(kind) or ():
            for phrase in role_phrases(text, facts.name or name):
                roles.update(word for word in words(phrase) if word not in NOISE and word not in identity)
    title = {word: 1.0 if word in roles else round(ELSEWHERE * min(1.0, count / 2), 4)
             for word, count in counts.items()}
    for word in roles:
        title.setdefault(word, 1.0)
    title.update(dict.fromkeys(read_.negated - set(counts) - roles, -ELSEWHERE * NEGATED))
    vector = {word: (1 + math.log(count)) * rarity(word, read_.language_of.get(word))
              for word, count in counts.items() if word not in NOISE}
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    # The CV reader's years, the one answer to how long they have worked: a reader of nine languages beside it
    # measured +0.011, inside the noise, and was a second answer to one question (2026-11-16).
    years = facts.years
    level = MIDDLE if years is None else 1 if years < 2 else 2 if years < 5 else 3
    held_levels = [level_ for each in facts.jobs[:2] if (level_ := title_level(each.title)) is not None]
    if held_levels and max(held_levels) >= 3:
        level = max(level, *held_levels)
    return {"title": title, "vector": {word: round(value / norm, 5) for word, value in vector.items()},
            "level": level, "spoken": sorted(spoken), **requirements.person(documents)}


class _Read:
    """One person's documents read a line at a time: how often each word comes, in which language, and which
    words they said they lack. A CV's first four lines count twice."""

    def __init__(self, identity: set[str], name: str | None):
        self.identity, self.name = identity, name
        self.counts: Counter = Counter()
        self.language_of: dict[str, str] = {}
        self.negated: set[str] = set()
        self.spoken: set[str] = set()

    def document(self, kind: str, text: str) -> None:
        language = detect_language(text) or "en"
        self.spoken.add(language)
        lines = [line for line in text.splitlines() if line.strip()]
        for at, line in enumerate(lines):
            if not (IDENTITY.search(fold(line)) or (self.name and self.name in fold(line))):
                self._line(line, language, kind in _CV_KINDS, head=kind in _CV_KINDS and at < 4)

    def _line(self, line: str, language: str, cv: bool, *, head: bool) -> None:
        for sentence in re.split(r"(?<=[.!?;])\s+", line):
            found = [word for word in words(sentence) if word not in self.identity]
            if not cv and NEGATION.search(fold(sentence)):
                self.negated.update(found)
                continue
            for word in found:
                self.counts[word] += 2 if head else 1
                self.language_of.setdefault(word, language)


def profile(db: Database, user: User) -> dict | None:
    """Their profile as stored, made again first when their documents changed since (`documents_changed_at`,
    which every upload, switch, deletion and About you stamps) or `VERSION` moved; a new name or link changes
    no score and makes none again (owner, 2026-11-16). The one writer of `users.fit_profile`, so it cannot be
    stale. None with no documents."""
    stored = user.fit_profile or {}
    changed = user.documents_changed_at or datetime.min.replace(tzinfo=UTC)
    current = stored.get("version") == VERSION and user.fit_profile_at is not None
    if current and user.fit_profile_at >= changed:
        return stored.get("profile")
    from libs.suggestions import documents_of  # noqa: PLC0415 - suggestions imports matching, which imports this
    made = person(documents_of(db, user.id), user.full_name)
    at = now()
    kept = {"version": VERSION, "profile": made}
    db[User.COLLECTION].update_one({"_id": user.id}, {"$set": {"fit_profile": kept, "fit_profile_at": at}})
    user.fit_profile, user.fit_profile_at = kept, at
    logger.info("fit: profile of %s made from their documents (%s)", user.id,
                "no text" if made is None else f"{len(made['vector'])} words")
    return made


# ── their list's scores, kept to filter by ─────────────────────────────────────────────────────

def key(made: dict) -> str:
    """What a kept score was worked out with: their profile, the model and `VERSION`. A score kept with
    another key is not theirs any more and is read as missing, so nothing has to find and delete it."""
    return hashlib.sha256(json.dumps([VERSION, model(), made], sort_keys=True).encode()).hexdigest()[:16]


def kept(db: Database, user_id: _uuid.UUID, made: dict) -> dict[int, float]:
    """The scores kept for this person's profile as it is now, by job: 29 ms for 5,139, most of it reading
    them into Python; an index holding all four fields measured the same (2026-11-16)."""
    return {row["job_id"]: row["fit"] for row in db[FitScore.COLLECTION].find(
        {"user_id": user_id, "key": key(made)}, {"_id": 0, "job_id": 1, "fit": 1})}


def any_kept(db: Database, user_id: _uuid.UUID, made: dict) -> bool:
    """Whether anything is kept for this person's profile as it is now: no, once their documents change."""
    return db[FitScore.COLLECTION].count_documents({"user_id": user_id, "key": key(made)}, limit=1) > 0


def scored(made: dict) -> list[dict]:
    """Stages that give the postings of a page their score for this person, `fit_score`, worked out there and
    then: 25 jobs, about 11 ms, whatever the list holds (2026-11-16). Their features leave with them."""
    return [{"$addFields": {"fit_score": expression(made)}}, {"$unset": "fit"}]


def at_least(made: dict, kept_: dict[int, float], shown_: int) -> dict:
    """A condition on a posting: its score for this person shows as `shown_` or more. Read from `kept_`
    (`kept`) where it holds one, as a list of jobs the database looks up by their number; worked out there and
    then for the rest. Kept or not, the answer is the same; keeping makes it quick over a whole list."""
    floor = shown_ - 0.5
    return {"$or": [{"_id": {"$in": sorted(job for job, score in kept_.items() if score >= floor)}},
                    {"_id": {"$nin": sorted(kept_)}, "$expr": {"$gte": [expression(made), floor]}}]}


def unkept(kept_: dict[int, float]) -> dict:
    """A condition on a posting: it has no score kept for this person (`kept`) and could have one."""
    return {"_id": {"$nin": sorted(kept_)}, "fit_version": VERSION}


# Whose scores this process is keeping now, so a page that asks while a run is under way adds no second one.
# Held in this process alone and let go however a run ends: nothing is left claimed by a run that failed or a
# process that stopped, and no clock decides when a run is over (owner, 2026-11-16: "no background stuck
# timeouts"). Two processes keeping one person's scores at once write the same rows.
_keeping: set[_uuid.UUID] = set()
_keeping_lock = Lock()


def fill(db: Database, user_id: _uuid.UUID) -> None:
    """A score kept for every job of their list that has none for their profile as it is: the whole list after
    their documents change, the new jobs after that, all in the database. Each kept until its job is archived.
    Run after a page that found nothing kept, or had to work a score out (`unkept`)."""
    from libs.corpus.freshness import platform_windows, shelf_life  # noqa: PLC0415 - matching imports this module
    from libs.matching import _read, marks, scan_for  # noqa: PLC0415
    from libs.settings import corpus_retention_days  # noqa: PLC0415

    with _keeping_lock:
        if user_id in _keeping:
            return
        _keeping.add(user_id)
    clock = monotonic()
    try:
        user = User.from_doc(db[User.COLLECTION].find_one({"_id": user_id}))
        made = None if user is None else profile(db, user)
        skipped, applied = marks(db, user_id)
        _, longest = shelf_life(platform_windows(db))
        stages = None if made is None else _read(db, user, scan=scan_for(longest), fit=True,
                                                 skip=skipped | set(applied))
        if stages is None:
            return
        have = sorted(kept(db, user_id, made))
        kept_for = corpus_retention_days(db) * 86_400_000  # in milliseconds, as dates are added
        db[CorpusJob.COLLECTION].aggregate([
            # A posting whose features are older than `VERSION` is kept once `catch_up` has read it again.
            *stages, {"$match": {"fit_version": VERSION, "_id": {"$nin": have}}},
            {"$project": {"_id": 0, "user_id": {"$literal": user_id}, "job_id": "$_id",
                          "fit": expression(made), "key": {"$literal": key(made)},
                          # Archived when it was found that long ago (`libs.corpus.archive._due`).
                          "expires_at": {"$add": ["$first_seen_at", kept_for]}}},
            {"$match": {"fit": {"$ne": None}}},
            {"$merge": {"into": FitScore.COLLECTION, "on": ["user_id", "job_id"], "whenNotMatched": "insert",
                        "whenMatched": [{"$set": {"fit": "$$new.fit", "key": "$$new.key",
                                                  "expires_at": "$$new.expires_at"}}]}}])
        logger.info("fit: the list of %s kept in %d ms (%d had a score)", user_id,
                    (monotonic() - clock) * 1000, len(have))
    except Exception:
        logger.exception("fit: keeping the scores of %s failed; its pages work them out meanwhile", user_id)
    finally:
        with _keeping_lock:
            _keeping.discard(user_id)


def shown(score: float) -> int:
    """A score as a page shows it, 1 to 10: rounded half up, which is what `at_least` filters by."""
    return max(1, min(10, math.floor(score + 0.5)))


# ── the score ──────────────────────────────────────────────────────────────────────────────────

@cache
def model() -> dict:
    """The weights of the four signals and the steps from their sum to 1-10, fitted by
    `scripts/cvlab/fit/model.py`."""
    return json.loads((_HERE / "model.json").read_text(encoding="utf-8"))


def _arrays(made: dict) -> tuple[list[float], list[float]]:
    """The person's two weights for each position. Two of their words in one position: the stronger title
    weight, and the sum of their vector weights, as the evaluation measured them."""
    title, vector = [0.0] * POSITIONS, [0.0] * POSITIONS
    for word, value in made["title"].items():
        at = position(word)
        title[at] = value if title[at] == 0 else max(title[at], value)
    for word, value in made["vector"].items():
        vector[position(word)] += value
    return title, vector


def _weighted(positions: str, weights: str, values: list[float]) -> dict:
    """Σ weight × the person's value at the job's position, over a job's array."""
    indexes = {"$range": [0, {"$size": {"$ifNull": [positions, []]}}]}
    return {"$sum": {"$map": {"input": indexes, "as": "i", "in": {
        "$multiply": [{"$arrayElemAt": [weights, "$$i"]},
                      {"$arrayElemAt": [{"$literal": values}, {"$arrayElemAt": [positions, "$$i"]}]}]}}}}


SIGNALS = ("title", "cos", "up", "blocked")


def signals(made: dict) -> dict:
    """The four signals, as expressions over a posting's `fit`, for `$let`: how much of the job's title is
    their role (counting less while the texts are unalike), how alike the texts are, how many levels above
    them the job is, and whether it needs a language they do not speak. `expression` weighs them;
    `scripts/cvlab/fit/model.py` fits the weights from these same expressions, so what is measured is what
    the site runs."""
    title, vector = _arrays(made)
    return {"cos": _weighted("$fit.vh", "$fit.vw", vector),
            "title": _weighted("$fit.th", "$fit.tw", title),
            "up": {"$max": [0, {"$subtract": [{"$ifNull": ["$fit.level", made["level"]]}, made["level"]]}]},
            "blocked": {"$cond": [{"$setIsSubset": [{"$ifNull": ["$fit.needs", []]}, made["spoken"]]}, 0, 1]}}


def gated(title: str, cos: str) -> dict:
    """A title that matches counts fully only when the texts are alike too: "Data Warehouse Engineer" is not a
    warehouse job however much of its title a warehouse operative has."""
    return {"$multiply": [title, {"$min": [1, {"$divide": [cos, GATE]}]}]}


def expression(made: dict, fitted: dict | None = None) -> dict:
    """How well each posting fits this person, 1 to 10 with a fraction that orders the ones that round alike,
    worked out by the database from the posting's `fit` (`job`); None for a posting not read yet. Pages show
    it rounded. A job they cannot take shows at most `CEILING`. `fitted` is a model other than `model.json`,
    for the evaluation that judges one before it is written."""
    weights, steps = (fitted or model())["weights"], (fitted or model())["steps"]
    raw = {"$add": [weights["const"], {"$multiply": [weights["title"], gated("$$title", "$$cos")]},
                    *({"$multiply": [weights[name], f"$${name}"]} for name in ("cos", "up", "blocked"))]}
    calibrated = {"$switch": {"branches": [{"case": {"$lt": ["$$raw", upper]}, "then": value}
                                           for upper, value in steps[:-1]], "default": steps[-1][1]}}
    shown = {"$cond": [barred(made), {"$min": [calibrated, CEILING]}, calibrated]}
    return {"$cond": [{"$eq": [{"$ifNull": ["$fit", None]}, None]}, None, {"$let": {
        "vars": signals(made),
        "in": {"$let": {"vars": {"raw": raw},
                        "in": {"$add": [shown, {"$multiply": [1e-3, "$$raw"]}]}}}}}]}


def barred(made: dict) -> dict:
    """Whether a posting is one they cannot take, as an expression over its `fit`: it requires a credential of
    which they hold none of the alternatives, half held counting as held; it is open only to its own staff; or
    only to students, and they are not one (`requirements`)."""
    lacking = {"$anyElementTrue": [{"$map": {"input": {"$ifNull": ["$fit.req", []]}, "as": "group", "in": {
        "$eq": [{"$size": {"$setIntersection": ["$$group", {"$literal": made["has"]}]}}, 0]}}}]}
    return {"$or": [lacking, {"$eq": ["$fit.staff", True]},
                    {"$and": [{"$eq": ["$fit.student", True]}, {"$literal": not made["student"]}]}]}


