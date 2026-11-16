"""`libs.fit.person` and `libs.fit.job` with switches for the candidates: with every switch at its default
they give exactly what the shipped functions give (`check()` asserts it), so a difference measured is the
switch's alone."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from libs import fit
from libs.documents.read import fold, read
from libs.fit import ELSEWHERE, NEGATED, WORDS, _Read, rarity
from libs.fit.text import (
    MIDDLE,
    NOISE,
    core_title,
    languages_required,
    languages_spoken,
    title_level,
    words,
    working_text,
)
from scripts.cvlab.fit.build import PANEL, documents
from scripts.cvlab.fit.research.roles import core_words, role_phrases, split_heads


def kinds_of(name: str) -> dict[str, list[str]]:
    """A panel person's documents by kind, as `model.profiles` reads them."""
    kinds: dict[str, list[str]] = {}
    for filename, text in documents(name):
        kinds.setdefault(re.sub(r"-\d+$", "", filename.rsplit(".", 1)[0]), []).append(text)
    return kinds


def name_of(name: str) -> str | None:
    return json.loads((PANEL / name / "candidate.json").read_text(encoding="utf-8")).get("name")


def person(  # noqa: PLR0912 - the shipped function with a switch a reader, so each variant is one line changed
    documents_: Mapping[str, Sequence[str]],
    name: str | None = None,
    *,
    first_lines: bool = True,
    held: bool = True,
    summaries: bool = True,
    phrases: bool = False,
    compounds: bool = False,
    phrase_count: int = 0,
    elsewhere: float = ELSEWHERE,
    recent: int | None = None,
) -> dict | None:
    """`libs.fit.person`, with the role words' sources switched: `first_lines` (the first four lines of a CV),
    `held` (the CV reader's job titles), `summaries` (a summary's first sentence), `phrases` (the role
    phrases of `roles.role_phrases`, the first `recent` of each CV only when given), `compounds` (a
    compound role word's head as a role word too). `phrase_count` adds to a role phrase word's count."""
    texts = [text for kind in documents_ for text in (documents_[kind] or ()) if text]
    if not texts:
        return None
    facts = read(documents_)
    identity = set(words(name)) | set(words(facts.name))
    read_ = _Read(identity, fold(facts.name) if facts.name else None)
    for kind, kept in documents_.items():
        for text in kept or ():
            read_.document(kind, text)
    counts, roles = read_.counts, (read_.roles if first_lines else set())
    spoken = set(facts.languages) | languages_spoken(texts) | read_.spoken
    if held:
        for job_ in facts.jobs:
            found = set(words(job_.title)) - NOISE - identity
            roles |= found
            for word in found:
                counts[word] += 2
    else:
        for job_ in facts.jobs:
            for word in set(words(job_.title)) - NOISE - identity:
                counts[word] += 2
    if summaries:
        for summary in facts.profiles:
            roles.update(word for word in words(summary.split(".")[0]) if word not in NOISE | identity)
    for skill in facts.skills:
        for word in set(words(skill)) - identity:
            counts[word] += 1
    added: set[str] = set()
    if phrases:
        for kind in ("cv", "linkedin"):
            for text in documents_.get(kind) or ():
                found_phrases = role_phrases(text, facts.name or name)
                for phrase in found_phrases[:recent] if recent else found_phrases:
                    found = [word for word in words(phrase) if word not in NOISE and word not in identity]
                    if compounds:
                        found = [word for word in split_heads(found, phrase) if word not in identity]
                    roles.update(found)
                    added.update(found)
                    if phrase_count:
                        for word in set(found):
                            counts[word] += phrase_count
    title = {
        word: 1.0 if word in roles else round(elsewhere * min(1.0, count / 2), 4)
        for word, count in counts.items()
    }
    for word in added:
        title.setdefault(word, 1.0)
    title.update(dict.fromkeys(read_.negated - set(counts) - roles, -elsewhere * NEGATED))
    vector = {
        word: (1 + math.log(count)) * rarity(word, read_.language_of.get(word))
        for word, count in counts.items()
        if word not in NOISE
    }
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    years = facts.years
    level = MIDDLE if years is None else 1 if years < 2 else 2 if years < 5 else 3
    held_levels = [level_ for each in facts.jobs[:2] if (level_ := title_level(each.title)) is not None]
    if held_levels and max(held_levels) >= 3:
        level = max(level, *held_levels)
    return {
        "title": title,
        "vector": {word: round(value / norm, 5) for word, value in vector.items()},
        "level": level,
        "spoken": sorted(spoken),
        "years": years,
    }


def job(
    title: str,
    description: str | None,
    language: str | None,
    *,
    core: str = "current",
    compounds: bool = False,
) -> dict:
    """`libs.fit.job`, with the title's core read by `core`: "current" (`text.core_title`) or "heads"
    (`roles.core_words`, every part that names a job), and compound heads split off when `compounds`."""
    if core == "current":
        head = [word for word in dict.fromkeys(words(core_title(title))) if word not in NOISE]
        if compounds:
            head = split_heads(head, core_title(title))
    else:
        head = core_words(title, compounds=compounds)
    counts = Counter(words(working_text(description)))
    for word in words(title):
        counts[word] += 3
    vector = {
        word: (1 + math.log(count)) * rarity(word, language)
        for word, count in counts.items()
        if word not in NOISE
    }
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    top = sorted(vector.items(), key=lambda item: (-item[1], item[0]))[:WORDS]
    total = sum(rarity(word, language) for word in head) or 1.0
    needs = set(languages_required(description))
    return {
        "th": [fit.position(word) for word in head],
        "tw": [round(rarity(word, language) / total, 4) for word in head],
        "vh": [fit.position(word) for word, _ in top],
        "vw": [round(value / norm, 4) for _, value in top],
        "level": title_level(title),
        "needs": sorted(needs | ({language} if language else set())),
    }


def profiles(names, **switches) -> dict[str, dict]:
    return {name: person(kinds_of(name), name_of(name), **switches) for name in names}


def check(postings: dict, names) -> None:
    """With every switch at its default, the same as the shipped functions."""
    for name in names:
        mine, shipped = person(kinds_of(name), name_of(name)), fit.person(kinds_of(name), name_of(name))
        mine.pop("years")
        assert mine == shipped, name
    for row in list(postings.values())[:300]:
        assert job(row["title"], row.get("description"), row.get("language")) == fit.job(
            row["title"], row.get("description"), row.get("language")
        ), row["id"]
