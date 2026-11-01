"""The green and red words a person might want, each with the reason in plain words. No AI.

Green comes from what they have done: the job titles they held, then the skills they list that real
job titles here also name. Red comes from what does not fit them: seniority their years do not match,
pay that is not pay, and the words their own green words keep dragging in from jobs they have never
done ("sales", for an engineer whose "engineer" finds "Sales Engineer").

Stored on `users.suggestions` and recomputed whenever what it is made from changes — a document
saved, edited or deleted, or their search saved — so reading them costs one column.
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from collections import Counter

from pymongo.database import Database

from libs.contracts.config import migrate_config
from libs.corpus.geo import read_country
from libs.db import get
from libs.db.models import CorpusJob, User, UserDocument
from libs.documents.read import Facts, fold, read, stem, words
from libs.matching import SCAN, matching_jobs, norm, pattern, score
from libs.tenant.repository import TenantRepository

logger = logging.getLogger(__name__)

# How many of each are offered at once: titles held, skills, and words their matches drag in.
HELD_TITLES, SKILLS_OFFERED, DRIFT_WORDS = 3, 5, 5
# Under FEW_YEARS of experience senior roles are a long shot; from MANY_YEARS junior ones are behind them.
FEW_YEARS, MANY_YEARS = 3, 5
# How many of their matches a word must turn up in before it "keeps appearing".
DRIFT_MIN = 3

# The most words a suggestion has. A board searches a phrase as one thing, so "team coordinator & co
# lead" finds almost nothing while "team coordinator" finds the jobs (owner, 2026-11-01).
MAX_WORDS = 2

_SENIOR = ("senior", "lead", "principal", "head of")
_JUNIOR = ("intern", "internship", "graduate", "junior", "trainee")
_PAY = {"unpaid": "Jobs that pay nothing",
        "commission only": "Jobs that pay only when you sell"}
# Title words that say how, where, when or how senior, never what the work is. Places are left to geo.
_NOT_THE_WORK = frozenset("""
    and the for with of in at to on or an de der die das und mit für la le et en el y
    remote hybrid onsite site office home based from anywhere worldwide global international
    full part time contract permanent temporary temp freelance fixed term hourly shift shifts
    job jobs role position vacancy opportunity hiring urgent immediate start new team
    eu emea europe european dach cee nordics apac latam uk us usa all genders gender gn
    senior lead principal head intern internship graduate junior trainee mid level entry sr jr ii iii iv
    unpaid commission only
    """.split())  # noqa: SIM905 - a word list reads best as words


def compute(facts: Facts, *, config: dict | None, titles: list[str], matched: list[str],
            text: str, dismissed: set[str]) -> dict:
    """The suggestions, from their documents and two lists of corpus titles.

    `titles` are the newest job titles collected; `matched` the titles of the jobs their green words
    find today; `text` is every document they have, which a red word must appear nowhere in.
    """
    sections = (migrate_config(config) if config else {}).get("sections") or []
    have_green = {norm(word) for section in sections for word in section.get("keywords") or []}
    taken = have_green | dismissed | {
        norm(word) for section in sections for word in section.get("exclude_keywords") or []}

    def take(candidates, limit: int | None = None) -> list[dict]:
        """The first `limit` candidates nobody has yet: not in their search, dismissed or offered."""
        out: list[dict] = []
        for term, reason in candidates:
            term = norm(term)  # noqa: PLW2901 - compared in the form green words are matched in
            if ((limit is None or len(out) < limit) and 2 <= len(term) <= 40
                    and len(term.split()) <= MAX_WORDS and term not in taken):
                taken.add(term)
                out.append({"term": term, "reason": reason})
        return out

    green = take(_held(facts), HELD_TITLES) + take(_named(facts, titles), SKILLS_OFFERED)
    greens = have_green | {each["term"] for each in green}
    red = (take(_seniority(facts, greens)) + take(_PAY.items())
           + take(_dragged_in(matched, text, greens), DRIFT_WORDS))
    return {"green": green, "red": red}


def _held(facts: Facts):
    """The job titles they held, most recent first, each as one or two words: a title that names two
    jobs ("Software Engineer & Developer") offers both, and a long one is cut to its last two words,
    which are the job ("Full Stack Software Engineer" is a software engineer)."""
    for job in facts.jobs:
        reason = f"Your job title at {job.organisation}" if job.organisation else "A job title from your CV"
        for part in re.split(r"\s*(?:&|\+|/|\band\b)\s*", _plain_title(job.title), flags=re.I):
            if part.split():
                yield " ".join(part.split()[-MAX_WORDS:]), reason


def _named(facts: Facts, titles: list[str]) -> list[tuple[str, str]]:
    """Their skills that job titles here also name, the most named first."""
    regex = pattern([norm(skill) for skill in facts.skills])
    named: Counter[str] = Counter()
    example: dict[str, str] = {}
    for title in titles if regex else ():
        for word in score(regex, title, None)[1]:
            named[word] += 1
            example.setdefault(word, title)
    return [(word, f"One of your skills, and in {count} job title{'' if count == 1 else 's'} here, "
                   f"like “{example[word]}”") for word, count in named.most_common()]


def _seniority(facts: Facts, greens: set[str]) -> list[tuple[str, str]]:
    """The seniority their years are furthest from, never a word inside one of their green words."""
    if facts.years is None or FEW_YEARS <= facts.years < MANY_YEARS:
        return []
    few = facts.years < FEW_YEARS
    reason = (f"You have {facts.years} year{'' if facts.years == 1 else 's'} of experience, and "
              f"these jobs are usually for {'people with more' if few else 'people just starting out'}")
    return [(word, reason) for word in (_SENIOR if few else _JUNIOR)
            if not any(re.search(rf"(?<!\w){word}(?!\w)", green) for green in greens)]


def _dragged_in(matched: list[str], text: str, greens: set[str]) -> list[tuple[str, str]]:
    """Words that keep appearing in the titles their green words find, and nowhere in their documents."""
    known = words(text) | words(" ".join(greens))
    counts: Counter[str] = Counter()
    seen_in: dict[str, str] = {}
    for title in matched:
        for word in set(re.findall(r"[^\W\d_]{2,}", fold(title))):
            if word not in _NOT_THE_WORK and stem(word) not in known:
                counts[word] += 1
                seen_in.setdefault(word, title)
    return [(word, f"In {count} of the {len(matched)} jobs your words find, like “{seen_in[word]}”, "
                   "and nowhere in your documents")
            for word, count in counts.most_common() if count >= DRIFT_MIN and read_country(word) is None]


def _plain_title(title: str) -> str:
    """The title as a search word: "Senior Backend Engineer (Contract)" finds every backend engineer."""
    words_ = re.split(r"\s[-–—|/]\s|,", re.sub(r"\(.*?\)", "", title))[0].split()  # noqa: RUF001
    while words_ and fold(words_[0]).rstrip(".") in ("senior", "sr", "junior", "jr", "lead", "principal"):
        words_.pop(0)
    while words_ and re.fullmatch(r"(?i)i{1,3}|iv|\d+", words_[-1]):
        words_.pop()
    return " ".join(words_)


def documents_of(db: Database, user_id: _uuid.UUID) -> dict[str, str]:
    """Their documents' text by kind, newest first where they have several of one kind."""
    texts: dict[str, list[str]] = {}
    for row in db[UserDocument.COLLECTION].find({"user_id": user_id}, {"kind": 1, "text": 1},
                                                sort=[("uploaded_at", -1), ("_id", -1)]):
        texts.setdefault(row["kind"], []).append(row["text"])
    return {kind: "\n\n".join(parts) for kind, parts in texts.items()}


def refresh(db: Database, user_id: _uuid.UUID) -> dict:
    """Recompute and store this person's suggestions, keeping what they dismissed.

    Never raises. A CV the reader cannot follow must not cost the person the upload that triggered
    this, so a failure is logged and the previous suggestions stand.
    """
    user = get(db, User, user_id)
    previous = user.suggestions or {"green": [], "red": [], "dismissed": []}
    dismissed = previous.get("dismissed") or []
    try:
        documents = documents_of(db, user_id)
        config = TenantRepository(db).get_config(user_id)
        titles = [row["title"] for row in db[CorpusJob.COLLECTION].find(
            {}, {"title": 1}, sort=[("first_seen_at", -1)], limit=SCAN)]
        matched = [match.job.title for match in matching_jobs(db, user)]
        fresh = compute(read(documents), config=config, titles=titles, matched=matched,
                        text="\n".join(documents.values()), dismissed=set(dismissed))
    except Exception:  # see the docstring: the save that triggered this must survive it
        logger.exception("suggestions: could not work them out for user %s; the previous ones stand",
                         user_id)
        return previous
    suggestions = _store(db, user_id, {**fresh, "dismissed": dismissed})
    logger.info("suggestions: user %s has %d green and %d red, from %d document kind(s) and %d match(es)",
                user_id, len(fresh["green"]), len(fresh["red"]), len(documents), len(matched))
    return suggestions


def current(db: Database, user_id: _uuid.UUID) -> dict:
    """What is stored; worked out now for an account that has never had any."""
    user = get(db, User, user_id)
    return user.suggestions if user.suggestions is not None else refresh(db, user_id)


def dismiss(db: Database, user_id: _uuid.UUID, term: str) -> dict:
    """Never suggest this word again."""
    stored, key = current(db, user_id), norm(term)
    return _store(db, user_id, {
        **{kind: [each for each in stored.get(kind) or [] if each["term"] != key]
           for kind in ("green", "red")},
        "dismissed": sorted({*(stored.get("dismissed") or []), key})})


def _store(db: Database, user_id: _uuid.UUID, suggestions: dict) -> dict:
    db[User.COLLECTION].update_one({"_id": user_id}, {"$set": {"suggestions": suggestions}})
    return suggestions
