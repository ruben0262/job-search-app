"""Which jobs a person sees: their green words pick them, their red words veto them. Nothing learns.

The one definition behind the jobs page and the alerts (CLAUDE.md §1): both read `_read`, the alerts through
`matching_jobs` and the Jobs page through `page`. Red words, language, place, the way of working and the
freshness window are `libs.tenant.filters`; this module adds the green words, the minimum salary and the
order; all of it is asked of the database.
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from pymongo.database import Database

from libs.contracts.config import migrate_config
from libs.corpus import pay
from libs.corpus.freshness import PlatformWindows, platform_windows
from libs.corpus.geo import continent_of, countries_in
from libs.corpus.words import norm
from libs.db import find
from libs.db.models import CorpusJob, User, UserConfig, UserJobState, Word
from libs.db.models.corpus import WORDS_INDEX
from libs.db.models.user import LIGHT
from libs.membership import FREE, MEMBER, STARTER, jobs_as_of, members, starters, wait_hours
from libs.tenant.filters import UserFilters, candidate_conditions, filters_for
from libs.tenant.repository import TenantRepository

logger = logging.getLogger(__name__)

# How many of a person's newest jobs one call reads, for a day of postings asked for (`scan_for`).
SCAN = 2_000
# PCRE's `\w` is ASCII unless told otherwise; this makes a word boundary mean what Python's does.
_UNICODE = "(*UCP)"


@dataclass(frozen=True)
class Match:
    job: CorpusJob
    # The green words found, in the title or anywhere in the text: why the job is on the page.
    words: tuple[str, ...]
    # Those of them in the title, which says more than the text does.
    in_title: tuple[str, ...]
    # How well it fits them, 1 to 10 with a fraction (`libs.fit.scored`); None where it was not asked for.
    fit: float | None = None

    @property
    def rank(self) -> tuple[int, int]:
        """The more of their words a job has, the better it probably is; a title that names one
        breaks a tie (owner, 2026-11-02). Nothing is weighted or learned."""
        return len(self.words), len(self.in_title)


def green_words(config: dict | None) -> list[str]:
    """Every green word across the config's sections, once each."""
    sections = (migrate_config(config) if config else {}).get("sections") or []
    return sorted({norm(word) for section in sections
                   for word in section.get("keywords") or [] if word.strip()})


def pattern(words: list[str]) -> re.Pattern | None:
    """One regex for every word: whole words only, a space and a hyphen treated alike."""
    parts = [r"[\s-]+".join(map(re.escape, word.split())) for word in words]
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.I) if parts else None


def found(regex: re.Pattern, text: str | None) -> set[str]:
    """The words of `regex` that `text` has, each as words are compared."""
    return {norm(hit) for hit in regex.findall(text or "")}


def has_any(words: list[str]) -> dict:
    """A query condition: the title or the description has one of these words, judged in the database."""
    regex = _UNICODE + pattern(words).pattern
    return {"$or": [{"title": {"$regex": regex, "$options": "i"}},
                    {"$expr": {"$regexMatch": {"input": {"$ifNull": ["$description", ""]}, "regex": regex,
                                               "options": "i"}}}]}


class Matches(list):
    """A person's matches, and whether the read stopped at its budget (`scan`) with more left unread: then
    they are the newest of their matches, not all of them, and a page says so."""

    cut = False


def scan_for(hours: int) -> int:
    """The budget for a read reaching `hours` back: `SCAN` a day. A week of a broad search is seven days'
    postings, and at one day's budget the Jobs page showed the same day and a half whatever the job shelf
    life was: 898 of the 3,210 a week held for the dev copy's words, 334 ms against 807 for all of them
    (2026-11-07)."""
    return SCAN * max(1, -(-hours // 24))


# A posting whose words have not been looked for yet (`libs.corpus.terms`): its text is read instead.
_UNSEARCHED = {"$eq": [{"$ifNull": ["$terms_at", None]}, None]}
# The orders a list comes in, as the database sorts it: the most of their words first, then those in the
# title, then the newest found (owner, 2026-11-02); the newest posted first, the same way after that; or the
# last to reach the list first.
ORDERS = {
    "best": {"nwords": -1, "ntitle": -1, "first_seen_at": -1, "_id": -1},
    "newest": {"when": -1, "nwords": -1, "ntitle": -1, "first_seen_at": -1, "_id": -1},
    "arrived": {"described_at": -1, "_id": -1},
}
# What the read adds to a posting, taken off again before it is a `CorpusJob`.
_ADDED = ("words", "in_title", "nwords", "ntitle", "when", "_visible", "fit_score")


def _has(word: str, stored: str, fields: tuple[str, ...], by_text: bool) -> dict:
    """An expression: whether a posting has one of a person's words, as the words index finds a word
    (`libs.corpus.words.words_in`), each on its own: from the words stored on the posting where it was
    searched for that word, else read in `fields` with the word's own pattern."""
    read = {"$or": [{"$regexMatch": {"input": {"$ifNull": [field, ""]}, "options": "i",
                                     "regex": _UNICODE + pattern([word]).pattern}} for field in fields]}
    return read if by_text else {"$cond": [_UNSEARCHED, read, {"$in": [word, {"$ifNull": [stored, []]}]}]}


def _words(mine: list[str], pending: set[str]) -> dict:
    """A stage: each posting's `words`, the person's words it has anywhere, and `in_title`, those in its
    title, said as the person's own words. A word not searched for yet (`pending`) is read in the text."""
    def which(stored: str, fields: tuple[str, ...]) -> dict:
        return {"$concatArrays": [{"$cond": [_has(word, stored, fields, word in pending), [word], []]}
                                  for word in mine]}
    return {"$addFields": {"words": which("$terms", ("$title", "$description")),
                           "in_title": which("$title_terms", ("$title",))}}


def _asked(config: dict | None, filters: UserFilters,
           as_of: datetime | None) -> tuple[list[str], list[dict]] | None:
    """What one person's list asks of a posting: their green words, one of which it must have, and every other
    condition (`candidate_conditions`, the plan's wait, a minimum salary). None when they have no words. The
    one definition behind `_read` and `on_lists`."""
    mine = green_words(config)
    if not mine:
        return None
    where = candidate_conditions(filters)
    if as_of is not None:
        # Found by then, which the words index reads by and stops at; and read by then, the rule itself.
        where += [{"first_seen_at": {"$lte": as_of}}, {"described_at": {"$lte": as_of}}]
    if (floor := pay.floor_eur(config)) is not None:
        where.append({"$or": [{"salary_top_eur": None}, {"salary_top_eur": {"$gte": floor}}]})
    return mine, where


def _read(db: Database, user: User, *, described_since: datetime | None = None,
          since: datetime | None = None, scan: int = SCAN, text: bool = False, fit: bool = False,
          skip: set[int] | frozenset[int] = frozenset(), only: int | None = None,
          delay: int | None = None, windows: PlatformWindows | None = None) -> tuple[list[dict], dict] | None:
    """The stages that read this person's matches, the newest `scan` of them, each with `words` and
    `in_title`: the one question behind the Jobs page and the alerts (CLAUDE.md §1). None when they have
    no words.

    A job is one once its whole text is read (`candidate_conditions`), so `described_since` keeps those that
    became one since: what alerts send. `since` is the oldest the caller will show, so the budget is spent
    inside it: a posting found a day before it can still have been posted inside it, by a board whose clock
    runs ahead of ours. `skip` is jobs never to read: those they skipped or applied to. `only` is the one job
    to read, if it is a match of theirs: where an alert's link lands, asked of that job alone and not found
    among the newest `scan`, which read every match to show one and missed a job older than they reach.

    Each posting says which of everyone's words it has (`libs.corpus.terms`), so a match is an index lookup.
    A posting not yet searched, and a word nobody's postings have been searched for yet, are read with a
    regex over the text, so nothing waits on the index. They are separate questions, joined in the database
    (`$unionWith`), not one with an "or": asked as one, the database read every posting with a person's
    words and sorted them; asked apart, it reads the index newest first and stops at `scan`. At 1.5 million
    postings the busiest person's match read 4,996 postings in 1.2 s against 160,000 in over two minutes
    (2026-11-04).

    A free account's are the jobs a member's list had `free_delay_hours` ago and still has
    (`libs.membership.jobs_as_of`), so a job reaches it a week late and leaves it when it leaves everybody's.
    A minimum salary is the top of a posting's stated pay (`libs.corpus.pay`), stored with its text: unknown
    is never underpaid.

    With the stages comes how the database is to run them (`aggregate(stages, **how)`): the words question
    first, read from the words index by name. Left to choose, it chose between that index and the newest
    postings from one run to the next, and by the second read every open posting to find a person's: 42,192
    of them for 5,883 matches, where the index reads 13,870 (215 ms against 119, 2026-11-20). Not for one job
    or for the jobs read since a moment ago, which are found faster by what names them.

    `delay` and `windows` are their plan's wait and the platform's windows, for a caller that has read them
    already (`libs.membership.wait_hours`, `platform_windows`): a page asked for the plan three times and
    the windows twice (2026-11-20)."""
    config = TenantRepository(db).get_config(user.id)
    asked = _asked(config, filters_for(db, user, windows=windows, config=config),
                   jobs_as_of(db, user, delay))
    if asked is None:
        return None
    mine, where = asked
    if described_since is not None:
        where.append({"described_at": {"$gte": described_since}})
    if since is not None:
        where.append({"first_seen_at": {"$gte": since - timedelta(days=1)}})
    if skip:
        where.append({"_id": {"$nin": sorted(skip)}})
    if only is not None:
        where.append({"_id": only})
    indexed = sorted(db[Word.COLLECTION].distinct("_id", {"_id": {"$in": mine}, "indexed_at": {"$ne": None}}))
    pending = {word for word in mine if word not in indexed}

    newest = [{"$sort": {"first_seen_at": -1, "_id": -1}}, {"$limit": scan}]
    # Its words found where its text is at hand, and the text left behind unless the caller shows it.
    # What the fit score reads of a posting leaves too, unless the caller scores the page it shows.
    left = [*([] if text else ["description"]), *([] if fit else ["fit"])]
    found_there = [_words(mine, pending), {"$match": {"words.0": {"$exists": True}}},
                   *([{"$unset": left}] if left else [])]
    searches = [[{"$match": {"$and": [*where, {"terms_at": None, **has_any(mine)}]}}, *newest, *found_there]]
    how: dict = {}
    if indexed:
        # Found by the words stored on it, and with every word of theirs stored, nothing below reads its
        # text: left behind before the newest are sorted and not after, so what is sorted is a tenth the
        # size. 567 ms to 456 for a list of 5,894 (2026-11-21). With a word still pending, the text is read
        # for it, and leaves after.
        early = [{"$unset": left}] if left and not pending else []
        # First: what the database is told about how to read applies to the question it begins with.
        searches.insert(0, [{"$match": {"$and": [*where, {"terms": {"$in": indexed}}]}}, *early, *newest,
                            *found_there])
        how = {} if only is not None or described_since is not None else {"hint": WORDS_INDEX}
    if pending:
        # A word not searched for yet, read in the newest candidates' text meanwhile. Only that word: when
        # every word was read this way, adding one cut a list from ~1,500 jobs to 458 (2026-11-04).
        searches.append([{"$match": {"$and": where}}, *newest, *found_there])
    first, *rest = searches
    joined = [{"$unionWith": {"coll": CorpusJob.COLLECTION, "pipeline": each}} for each in rest]
    # A posting two of the questions found is one posting.
    once = [{"$group": {"_id": "$_id", "row": {"$first": "$$ROOT"}}}, {"$replaceRoot": {"newRoot": "$row"}}]
    return [*first, *joined, *(once if rest else []), *newest,
            {"$addFields": {"nwords": {"$size": "$words"}, "ntitle": {"$size": "$in_title"},
                            "when": {"$ifNull": ["$posted_at", "$first_seen_at"]}}}], how


def _match(row: dict) -> Match:
    words, in_title = tuple(sorted(row["words"])), tuple(sorted(row["in_title"]))
    job = CorpusJob.from_doc({key: value for key, value in row.items() if key not in _ADDED})
    return Match(job, words, in_title, row.get("fit_score"))


def matching_jobs(db: Database, user: User, *, described_since: datetime | None = None,
                  since: datetime | None = None, scan: int = SCAN, text: bool = False,
                  skip: set[int] | frozenset[int] = frozenset()) -> Matches:
    """This person's matches, the most of their words first, then newest first (`_read`). Only the words
    come back, unless `text` asks for the description too, for a caller that shows it (an alert's salary
    line)."""
    read = _read(db, user, described_since=described_since, since=since, scan=scan, text=text, skip=skip)
    if read is None:
        return Matches()
    stages, how = read
    rows = list(db[CorpusJob.COLLECTION].aggregate([*stages, {"$sort": ORDERS["best"]}], **how))
    if described_since and len(rows) == scan:
        # The caller's watermark will pass every older match this never reached.
        logger.warning("matching: %s has at least %d new matches; only the newest %d were read, so older "
                       "ones will not be alerted (the jobs page still lists them)", user.id, scan, scan)
    matches = Matches(_match(row) for row in rows)
    # Cut short only if the budget ran out inside the time asked: spent in the day before it, it has read
    # all of it.
    matches.cut = len(rows) >= scan and (since is None or min(row["first_seen_at"] for row in rows) >= since)
    return matches


def offered(match: dict) -> dict:
    """The facets that count what a list's filters offer, over the jobs `match` keeps: each job site, country
    and town with how many of them have it, most first. A town is named with its country ("DE/Berlin"): two
    towns of one name are two places, and a town is picked inside its country (`places`)."""
    counted = [{"$sort": {"count": -1, "_id": 1}}]
    placed = {"country": {"$nin": [None, ""]}}
    return {"sources": [{"$match": match}, {"$sortByCount": "$source"}, *counted],
            "countries": [{"$match": {**match, **placed}}, {"$sortByCount": "$country"}, *counted],
            "cities": [{"$match": {**match, **placed, "city": {"$nin": [None, ""]}}},
                       {"$sortByCount": {"$concat": ["$country", "/", "$city"]}}, *counted]}


def offers(db: Database, ids: list[int]) -> dict[str, list[tuple[str, int]]]:
    """What the filters offer over these jobs (`offered`): a list somebody keeps (Bookmarks, Applied)."""
    [made] = db[CorpusJob.COLLECTION].aggregate([{"$match": {"_id": {"$in": ids}}}, {"$facet": offered({})}])
    return {name: [(row["_id"], row["count"]) for row in rows] for name, rows in made.items()}


def places(continents: list[str], countries: list[str], cities: list[str]) -> dict | None:
    """The condition a job meets to be in the places picked, or None when none is (owner, 2026-11-24: "narrow
    down continent, then country and then city ... choose multiple of each"): in a town picked ("DE/Berlin"),
    in a country picked none of whose towns is, or on a continent picked none of whose countries or towns is.
    A place picked inside another narrows it, and beside it adds to it: Germany and Berlin is Berlin; France
    and Berlin is all of France, and Berlin."""
    towns = [tuple(each.split("/", 1)) for each in cities]
    narrowed = {code for code, _ in towns}
    whole = {code for code in countries if code not in narrowed}
    inside = {continent_of(code) for code in {*countries, *narrowed}}
    whole |= set(countries_in([each for each in continents if each not in inside]))
    either = [*({"country": code, "city": town} for code, town in towns),
              *([{"country": {"$in": sorted(whole)}}] if whole else [])]
    return {"$or": either} if either else None


@dataclass
class Page:
    """One page of a person's Jobs list and what the page says around it, all made by the database."""
    matches: list[Match]
    # How many jobs the list holds with the filters on, and how many of them reached it after `found_after`.
    total: int
    arrived: int | None
    # What the filters offer, each with how many of the jobs shown in the time asked have it, most first
    # (`offered`): a town as "DE/Berlin".
    sources: list[tuple[str, int]]
    words: list[tuple[str, int]]
    countries: list[tuple[str, int]]
    cities: list[tuple[str, int]]
    cut: bool
    # How many jobs of the list `unkept` holds for (`libs.fit.unkept`).
    live: int = 0


def page(db: Database, user: User, *, since: datetime | None, scan: int, wanted: dict, order: str,
         offset: int, limit: int, found_after: datetime | None = None, fit: list[dict] | None = None,
         unkept: dict | None = None, only: int | None = None, counts: bool = False,
         delay: int | None = None, windows: PlatformWindows | None = None) -> Page:
    """A page of this person's Jobs list, as the database makes it: their matches (`_read`, less a job they
    skipped or applied to), those shown in the time asked (`since`, by when a job was posted, else found),
    narrowed by the page's own filters (`wanted`, a condition on a match, its `words` and `nwords`
    included), in `order`, from `offset`; and the counts beside the filters, over the time asked alone. Only
    the page leaves the database, so the cost does not grow with what the list holds the way reading all
    of it did: 1,025 ms at 15,554 jobs (2026-11-15).

    `fit` scores the page's jobs for this person, after the page is cut (`libs.fit.scored`); `unkept` counts
    the jobs of the list with no score kept for them (`live`). `only` makes the list one job, if it is theirs
    (`_read`): 4 ms where finding it in the whole list took 380 (2026-11-20). `counts` asks for how many there
    are and nothing else: no page, and none of what the filters offer."""
    skipped, applied = marks(db, user.id)
    read = _read(db, user, since=since, scan=scan, fit=fit is not None, skip=skipped | set(applied),
                 only=only, delay=delay, windows=windows)
    if read is None:
        return Page([], 0, None if found_after is None else 0, [], [], [], [], cut=False)
    stages, how = read
    shown = {"_visible": True}
    facets = {
        "read": [{"$count": "n"}],
        "oldest": [{"$sort": {"first_seen_at": 1}}, {"$limit": 1}, {"$project": {"first_seen_at": 1}}],
        **offered(shown),
        "words": [{"$match": shown}, {"$unwind": "$words"}, {"$sortByCount": "$words"},
                  {"$sort": {"count": -1, "_id": 1}}],
        "total": [{"$match": {"$and": [shown, wanted]}}, {"$count": "n"}],
        "page": [{"$match": {"$and": [shown, wanted]}}, {"$sort": ORDERS[order]}, {"$skip": offset},
                 {"$limit": limit}, *(fit or [])],
    }
    if counts:
        facets = {name: facets[name] for name in ("read", "oldest", "total")}
    if unkept is not None:
        facets["live"] = [{"$match": unkept}, {"$count": "n"}]
    if found_after is not None:
        facets["arrived"] = [{"$match": {"$and": [shown, wanted, {"described_at": {"$gte": found_after}}]}},
                             {"$count": "n"}]
    visible = True if since is None else {"$gte": ["$when", since]}
    [made] = db[CorpusJob.COLLECTION].aggregate([*stages, {"$addFields": {"_visible": visible}},
                                                 {"$facet": facets}], **how)
    count = {name: made[name][0]["n"] if made[name] else 0
             for name in ("read", "total", "arrived", "live") if name in made}
    oldest = made["oldest"][0]["first_seen_at"] if made["oldest"] else None
    return Page(
        matches=[_match(row) for row in made.get("page", [])], total=count["total"],
        arrived=count.get("arrived"),
        **{name: [(row["_id"], row["count"]) for row in made.get(name, [])]
           for name in ("sources", "words", "countries", "cities")},
        cut=count["read"] >= scan and oldest is not None and (since is None or oldest >= since),
        live=count.get("live", 0))


def marks(db: Database, user_id) -> tuple[set[int], dict[int, datetime]]:
    """The jobs this person skipped, and when they applied to which."""
    rows = list(db[UserJobState.COLLECTION].find(
        {"user_id": user_id, "$or": [{"dismissed_at": {"$ne": None}}, {"applied_at": {"$ne": None}}]},
        {"job_id": 1, "dismissed_at": 1, "applied_at": 1}))
    return ({row["job_id"] for row in rows if row["dismissed_at"]},
            {row["job_id"]: row["applied_at"] for row in rows if row["applied_at"]})


def their_jobs(db: Database, user: User, *, described_since: datetime | None = None,
               since: datetime | None = None, scan: int = SCAN) -> Matches:
    """What this person's Jobs page lists: their matches, less a job they skipped or applied to, which is
    gone from it for good; for a free account, only those a member's list had a week ago (`_read`)."""
    skipped, applied = marks(db, user.id)
    return matching_jobs(db, user, described_since=described_since, since=since, scan=scan,
                         skip=skipped | set(applied))


def on_lists(db: Database, wanted: dict[_uuid.UUID, list[int]]) -> dict[_uuid.UUID, set[int]]:
    """Which of these jobs each person's Jobs list has (`their_jobs`), asked of the database once for
    everybody:
    what an alert following My search catches (`libs.alerts`). Each person's conditions are `_asked`'s, as on
    their Jobs page, run as a facet over only these jobs; what they all read (their configs, plans, skipped
    jobs,
    the words index) is read once rather than once a person. One query a pass, where a query a person cost
    13.6 ms each for 428 people, 6.9 s, every minute (2026-11-17)."""
    if not wanted:
        return {}
    people = sorted(wanted)
    configs = {row["user_id"]: row["config"] for row in db[UserConfig.COLLECTION].find(
        {"user_id": {"$in": people}}, {"user_id": 1, "config": 1})}
    paying = set(db[User.COLLECTION].distinct("_id", {"$and": [{"_id": {"$in": people}}, members()]}))
    waiting = set(db[User.COLLECTION].distinct("_id", {"$and": [{"_id": {"$in": people}}, starters()]}))
    waits = {plan: wait_hours(db, plan) for plan in (FREE, STARTER, MEMBER)}
    windows = platform_windows(db)
    every = sorted({job for jobs in wanted.values() for job in jobs})
    gone = {(row["user_id"], row["job_id"]) for row in db[UserJobState.COLLECTION].find(
        {"user_id": {"$in": people}, "job_id": {"$in": every},
         "$or": [{"dismissed_at": {"$ne": None}}, {"applied_at": {"$ne": None}}]},
        {"user_id": 1, "job_id": 1})}
    indexed = set(db[Word.COLLECTION].distinct("_id", {"indexed_at": {"$ne": None}}))
    facets = {}
    for user in find(db, User, {"_id": {"$in": people}}, projection=LIGHT):
        plan = STARTER if user.id in waiting else MEMBER if user.id in paying else FREE
        config = configs.get(user.id)
        asked = _asked(config, filters_for(db, user, windows=windows, config=config),
                       jobs_as_of(db, user, waits[plan]))
        jobs = [job for job in wanted[user.id] if (user.id, job) not in gone]
        if asked is None or not jobs:
            continue
        mine, where = asked
        facets[str(user.id)] = [{"$match": {"$and": [*where, {"_id": {"$in": jobs}}]}},
                                _words(mine, {word for word in mine if word not in indexed}),
                                {"$match": {"words.0": {"$exists": True}}}, {"$project": {"_id": 1}}]
    if not facets:
        return {}
    [made] = db[CorpusJob.COLLECTION].aggregate([{"$match": {"_id": {"$in": every}}}, {"$facet": facets}])
    return {_uuid.UUID(person): {row["_id"] for row in rows} for person, rows in made.items()}
