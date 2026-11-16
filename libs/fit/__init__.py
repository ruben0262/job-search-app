"""How well a job fits a person, 1 to 10, from their documents alone. Rules and a fitted calibration; no AI
call (owner, 2026-11-15 and 2026-11-16; CLAUDE.md §0). Not the learned model under the tag
`model-on-postgres` (CLAUDE.md §3): nothing here learns per person, and nothing is stored per person per
job.

**What it compares.** A job's title, read for the job it names, against the words a person uses for their own
roles (headline, job titles, summary); and the job's text, less what it says about the company and the
package, against everything they wrote, as two vectors of words weighted by how rare each is in the corpus, in
its own language. A title that matches while the text does not counts less. A job a level or two above them,
or one that needs a language they do not speak, counts less. Those four signals, weighted and mapped to 1 to
10 by `model.json`, which `scripts/cvlab/fit/model.py` fits to readings of real postings against made-up
people of every trade.

**Not on the site yet** (owner, 2026-11-16: accuracy first). On 12 people nothing was fitted to, it orders a
list better than the Jobs page's own order for 10 of 12 and its top five better for 9 of 12, but its number is
1.7 points from the readers' on average and squeezed towards the middle (CHANGELOG 2026-11-16, evening).
What the site would run is what the bench measures: a job's features (`job`), a person's profile (`person`)
and the database expression (`expression`, over positions in arrays of `POSITIONS` weights). Scoring every
job of a long list that way measured 0.8 to 2.5 s per 15,000 jobs, a page of 25 11 ms, so how a filter by fit
is made waits for the model it would filter by.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import zlib
from collections import Counter
from collections.abc import Mapping, Sequence
from functools import cache

from libs.corpus.langfilter import detect_language
from libs.documents.read import fold, read
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

# What a job's features and a person's profile are made by: a change to how either is read moves it.
VERSION = 1
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
            "level": title_level(title), "needs": sorted(needs | ({language} if language else set()))}


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
    counts, roles = read_.counts, read_.roles
    spoken = set(facts.languages) | languages_spoken(texts) | read_.spoken
    for held in facts.jobs:
        found = set(words(held.title)) - NOISE - identity
        roles |= found
        for word in found:
            counts[word] += 2
    for summary in facts.profiles:
        roles.update(word for word in words(summary.split(".")[0]) if word not in NOISE | identity)
    for skill in facts.skills:
        for word in set(words(skill)) - identity:
            counts[word] += 1
    title = {word: 1.0 if word in roles else round(ELSEWHERE * min(1.0, count / 2), 4)
             for word, count in counts.items()}
    title.update(dict.fromkeys(read_.negated - set(counts), -ELSEWHERE * NEGATED))
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
            "level": level, "spoken": sorted(spoken)}


class _Read:
    """One person's documents read a line at a time: how often each word comes, in which language, which words
    they said they lack, and the words of the first lines of a CV, which name their role."""

    def __init__(self, identity: set[str], name: str | None):
        self.identity, self.name = identity, name
        self.counts: Counter = Counter()
        self.language_of: dict[str, str] = {}
        self.negated: set[str] = set()
        self.roles: set[str] = set()
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
            if head:
                self.roles.update(word for word in found if word not in NOISE)


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
    it rounded. `fitted` is a model other than `model.json`, for the evaluation that judges one before it is
    written."""
    weights, steps = (fitted or model())["weights"], (fitted or model())["steps"]
    raw = {"$add": [weights["const"], {"$multiply": [weights["title"], gated("$$title", "$$cos")]},
                    *({"$multiply": [weights[name], f"$${name}"]} for name in ("cos", "up", "blocked"))]}
    calibrated = {"$switch": {"branches": [{"case": {"$lt": ["$$raw", upper]}, "then": value}
                                           for upper, value in steps[:-1]], "default": steps[-1][1]}}
    return {"$cond": [{"$eq": [{"$ifNull": ["$fit", None]}, None]}, None, {"$let": {
        "vars": signals(made),
        "in": {"$let": {"vars": {"raw": raw},
                        "in": {"$add": [calibrated, {"$multiply": [1e-3, "$$raw"]}]}}}}}]}


