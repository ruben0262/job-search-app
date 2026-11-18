"""Alerts: named searches a person follows, each sending what it finds to the bell, their phone, Telegram or
the daily email (owner, 2026-11-17). Starter and Member have them (`libs.membership`).

**One pass for everybody, not a query per person.** Every ten seconds (`route`, on the notifier's own clock)
the jobs that joined a list since the last pass are read once, with only what an alert looks at, and every
alert is tested against each of them in memory (`Rule.catches`): a word is a lookup in the words a posting
already carries (`libs.corpus.terms`), a company, a place and a job site are a field each. Nobody costs a
query unless something of theirs was caught, and nothing reads what was sent before. The two questions that
need the database are asked only of what got that far: a match score (`libs.fit`) and "on my Jobs list"
(`libs.matching`, the Jobs page's own question, so the alert and the page cannot disagree). On 500 accounts
the per-account way it replaced spent 18.5 s a minute; see CHANGELOG 2026-11-17 for this one's number.

What was caught goes on the person's row for the job (`UserJobState.alerted_at`, `alerts`), once however many
of their alerts caught it, and the `notify` lane sends it (`services.notifier.deliver`).

**When a job reaches an alert** is when it joins the person's list: as soon as its whole text is read for a
member, and that long after for a plan that waits (`libs.membership.WAITS`; owner, 2026-11-17: Starter's
alerts keep its three days). So a pass reads once per wait, each from its own cursor (`AlertCursor`).
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import NamedTuple

from pymongo import UpdateOne
from pymongo.database import Database

from libs import fit
from libs.corpus.freshness import platform_windows, showable
from libs.corpus.geo import CITIES, COUNTRIES
from libs.corpus.repository import TYPED
from libs.corpus.terms import learn
from libs.corpus.words import norm, terms_of
from libs.db import find, insert, lease, next_ids, now
from libs.db.models import Alert, AlertCursor, CorpusJob, User, UserConfig, UserJobState, Word
from libs.matching import green_words, on_lists, pattern
from libs.membership import STARTER, having, starters, wait_hours
from libs.queue import enqueue_many

logger = logging.getLogger(__name__)

# The alert every account starts with: their Jobs list as it fills, what alerts were before 2026-11-17.
MY_SEARCH = "My search"
# How many new jobs one pass reads for one wait. Ten seconds bring a handful; a notifier back from a stop
# reads its backlog this many a pass rather than all at once.
PASS_JOBS = 2_000
# How long after a job's text is stamped it is read: the stamp and the write that stores it are a round trip
# apart, and a pass that read between the two would leave the job behind its cursor for good.
SETTLE = timedelta(seconds=10)
# How often a pass runs, on the notifier's own sweep (`services.notifier`): a job whose text is read now
# reaches a member's phone within about this long, `SETTLE` more, and the worker's idle wait at most:
# 10-25 s.
# Ten rather than sixty (2026-11-18, the owner: "speedy"): a pass with a handful of new jobs costs about
# what one with a minute's does, 380 ms against 813 ms for 500 broad accounts, so 2.3 s of work a minute
# against 0.8; one with nothing new, 47 ms.
EVERY_SECONDS = 10.0
# The most one pass holds the lease for: its own interval, so a process that stopped mid-pass lets the next
# one in when it is due, and no second clock decides it (CLAUDE.md §6).
LEASE_SECONDS = EVERY_SECONDS
# What a posting is read with: all an alert looks at.
_FIELDS = {"title": 1, "company": 1, "country": 1, "city": 1, "source": 1, "terms": 1, "terms_at": 1,
           "described_at": 1}


@dataclass(frozen=True)
class Rule:
    """An alert, made ready to test postings with."""

    alert: Alert
    keywords: frozenset[str]
    companies: re.Pattern | None
    countries: frozenset[str]
    cities: frozenset[str]
    sources: frozenset[str]
    # My search's words, for an alert that follows it: a job without one of them is not on the list. None when
    # one of them is not in the words index yet, so a posting's words cannot say, and the list itself decides.
    green: frozenset[str] | None = frozenset()

    @classmethod
    def of(cls, alert: Alert, green: frozenset[str] | None = frozenset()) -> Rule:
        return cls(alert=alert, keywords=frozenset(alert.keywords),
                   companies=pattern(alert.companies) if alert.companies else None,
                   countries=frozenset(code for place in alert.places if place in COUNTRIES
                                       for code in COUNTRIES[place].countries),
                   cities=frozenset(place for place in alert.places if place in CITIES),
                   sources=frozenset(alert.sources), green=green)

    def catches(self, job: dict) -> bool:
        """Whether a posting, as a pass reads it, is one for this alert: every field it was given holds. "On
        my Jobs list" is asked of the database after this, of what this let through; a job with none of My
        search's words cannot be on it, so it stops here."""
        terms = job.get("terms") or ()
        if self.alert.search and self.green is not None and self.green.isdisjoint(terms):
            return False
        if self.keywords and self.keywords.isdisjoint(terms):
            return False
        if self.companies and not self.companies.search(job.get("company") or ""):
            return False
        if (self.countries or self.cities) and job.get("country") not in self.countries \
                and job.get("city") not in self.cities:
            return False
        return not self.sources or job.get("source") in self.sources


class Pass(NamedTuple):
    """What a pass did: the new jobs it read, and the (person, job) it caught."""

    read: int = 0
    caught: int = 0


def route(db: Database) -> Pass:
    """One pass: every job that joined somebody's list since the last pass against every alert that may hear
    of it. Whoever had one caught is queued to be sent it. Run by one notifier at a time."""
    with lease(db, "alerts.route", LEASE_SECONDS) as mine:
        if not mine:
            return Pass()
        accounts = db[User.COLLECTION].distinct(
            "_id", {"$and": [{"active": True, "status": "active"}, having("alerts")]})
        waiting = set(db[User.COLLECTION].distinct("_id", {"$and": [{"_id": {"$in": accounts}}, starters()]}))
        starter_wait = wait_hours(db, STARTER)
        by_wait: dict[int, list[Alert]] = {0: [], starter_wait: []}
        for alert in find(db, Alert, {"on": True, "user_id": {"$in": accounts}}):
            by_wait[starter_wait if alert.user_id in waiting else 0].append(alert)
        done = [_pass(db, wait, alerts) for wait, alerts in by_wait.items()]
        return Pass(sum(each.read for each in done), sum(each.caught for each in done))


def _pass(db: Database, wait: int, alerts: list[Alert]) -> Pass:
    """The jobs that joined a list `wait` hours after their text was read, since this wait's cursor, against
    these alerts."""
    upto = now() - timedelta(hours=wait) - SETTLE
    cursor = db[AlertCursor.COLLECTION].find_one({"_id": str(wait)})
    # The first pass starts from now, alerts announce what is new, not what the Jobs page already shows, and
    # its empty read leaves the cursor there for the next. Returning here before writing it (`>=`) made every
    # pass the first, and no alert read a job on the live site (2026-11-18).
    since = cursor["at"] if cursor else upto
    if since > upto:
        return Pass()
    rows = _joined(db, since, upto, PASS_JOBS) if alerts else []
    reached = upto
    if len(rows) == PASS_JOBS:
        # Read up to the last whole moment; the jobs stamped with the one after are the next pass's.
        reached = rows[-1]["described_at"]
        rows = [row for row in rows if row["described_at"] < reached] or rows
        reached = rows[-1]["described_at"]

    caught = _caught(db, wait, alerts, _with_words(db, rows)) if rows else {}
    if caught:
        at = now()
        db[UserJobState.COLLECTION].bulk_write([UpdateOne(
            {"user_id": user_id, "job_id": job_id},
            # Once: a job caught again (a plan that changed its wait) keeps when it was first caught.
            [{"$set": {"alerted_at": {"$ifNull": ["$alerted_at", at]},
                       "alerts": {"$setUnion": [{"$ifNull": ["$alerts", []]}, sorted(ids)]},
                       "created_at": {"$ifNull": ["$created_at", at]}}}],
            upsert=True) for (user_id, job_id), ids in caught.items()], ordered=False)
        people = sorted({user_id for user_id, _ in caught})
        enqueue_many(db, "notify", people, dedup_key="due")
        logger.debug("alerts: %d new job(s) read for a %dh wait; %d caught for %d account(s)", len(rows),
                     wait, len(caught), len(people))
    db[AlertCursor.COLLECTION].update_one({"_id": str(wait)}, {"$set": {"at": reached}}, upsert=True)
    return Pass(len(rows), len(caught))


def would_have_found(db: Database, alert: Alert, days: int) -> int:
    """How many jobs `alert` would have found in the last `days` days: said as it is made or changed, so an
    alert that will find nothing is seen to at once rather than after days of silence. Asked as a pass asks,
    of the jobs that joined their list in those days, so it cannot count by another rule than the one that
    sends. A week is about 9,000 postings, read in 0.2 s (2026-11-17)."""
    starter = db[User.COLLECTION].count_documents({"$and": [{"_id": alert.user_id}, starters()]}, limit=1)
    wait = wait_hours(db, STARTER) if starter else 0
    upto = now() - timedelta(hours=wait)
    since = upto - timedelta(days=days)
    # As if made before them: otherwise it hears only of what joins after it was made.
    made_before = replace(alert, created_at=since)
    return len(_caught(db, wait, [made_before], _with_words(db, _joined(db, since, upto))))


def _joined(db: Database, since: datetime, upto: datetime, most: int = 0) -> list[dict]:
    """The postings whose text was read in `(since, upto]`, the oldest first, with what an alert looks at;
    `most` of them, or all."""
    return list(db[CorpusJob.COLLECTION].find(
        {"$and": [{"described_at": {"$gt": since, "$lte": upto}}, {"closed_at": None},
                  {"source": {"$ne": TYPED}}, showable(platform_windows(db))]},
        _FIELDS, sort=[("described_at", 1)], limit=most))


def _with_words(db: Database, rows: list[dict]) -> list[dict]:
    """Every posting with its words. Stored with its text (`CorpusRepository.set_description`), they are on
    the posting; one whose text was stored some other way is given them here, as they are found there
    (`libs.corpus.words.terms_of`), not passed over for a word it has."""
    if unsearched := [row["_id"] for row in rows if row.get("terms_at") is None]:
        vocabulary = sorted(db[Word.COLLECTION].distinct("_id"))
        texts = {doc["_id"]: doc.get("description") for doc in db[CorpusJob.COLLECTION].find(
            {"_id": {"$in": unsearched}}, {"description": 1})}
        for row in rows:
            if row["_id"] in texts:
                row["terms"] = terms_of(row.get("title"), texts[row["_id"]], vocabulary)["terms"]
    return rows


def _caught(db: Database, wait: int, alerts: list[Alert], rows: list[dict]) -> dict[tuple, set[int]]:
    """Which of these postings each person's alerts caught: `(user, job) -> alert ids`."""
    following = sorted({alert.user_id for alert in alerts if alert.search})
    configs = db[UserConfig.COLLECTION].find({"user_id": {"$in": following}}, {"user_id": 1, "config": 1})
    greens = {row["user_id"]: frozenset(green_words(row.get("config"))) for row in configs}
    # A word the postings are not searched for cannot be read off them: learnt now, for every later posting.
    known = set(db[Word.COLLECTION].distinct("_id"))
    learn(db, sorted({word for words in greens.values() for word in words} - known))
    rules = [Rule.of(alert, None if (green := greens.get(alert.user_id, frozenset())) - known else green)
             for alert in alerts]
    hits: dict[tuple, list[Alert]] = defaultdict(list)
    for row in rows:
        joined = row["described_at"] + timedelta(hours=wait)
        for rule in rules:
            if rule.alert.created_at <= joined and rule.catches(row):
                hits[rule.alert.user_id, row["_id"]].append(rule.alert)
    if not hits:
        return {}

    # The rest asks the database once for everybody, of only what was caught for each person.
    jobs_of: dict[_uuid.UUID, list[int]] = defaultdict(list)
    for user_id, job_id in hits:
        jobs_of[user_id].append(job_id)
    on_list = on_lists(db, {user_id: jobs_of[user_id] for user_id in _asking(hits, "search")})
    scores = _scores(db, find(db, User, {"_id": {"$in": sorted(_asking(hits, "min_fit"))}}), jobs_of)
    # A job they skipped or applied to has left their list for good, wherever it was found.
    gone = {(row["user_id"], row["job_id"]) for row in db[UserJobState.COLLECTION].find(
        {"user_id": {"$in": list(jobs_of)}, "job_id": {"$in": [row["_id"] for row in rows]},
         "$or": [{"dismissed_at": {"$ne": None}}, {"applied_at": {"$ne": None}}]},
        {"user_id": 1, "job_id": 1})}

    caught: dict[tuple, set[int]] = {}
    for (user_id, job_id), found in hits.items():
        score = scores.get(user_id, {}).get(job_id, 0)
        kept = {alert.id for alert in found if (user_id, job_id) not in gone
                and (not alert.search or job_id in on_list.get(user_id, ()))
                and (alert.min_fit is None or score >= alert.min_fit)}
        if kept:
            caught[user_id, job_id] = kept
    return caught


def _asking(hits: dict[tuple, list[Alert]], field: str) -> set[_uuid.UUID]:
    """Whoever had something caught by an alert that asks this (`search`, `min_fit`)."""
    return {user_id for (user_id, _), found in hits.items() if any(getattr(alert, field) for alert in found)}


def _scores(db: Database, users: list[User], jobs_of: dict[_uuid.UUID, list[int]]) -> dict[_uuid.UUID, dict]:
    """These people's match scores for the jobs caught for them, as a card shows them (`libs.fit.shown`), in
    one query: a facet each. Nothing for somebody without the documents a score is made from: an alert asking
    for one then catches nothing, and the Alerts page says why."""
    facets = {}
    for user in users:
        if (made := fit.profile(db, user)) is not None:
            facets[str(user.id)] = [
                {"$match": {"_id": {"$in": jobs_of[user.id]}, "fit_version": fit.VERSION}},
                {"$project": {"s": fit.expression(made)}}]
    if not facets:
        return {}
    every = sorted({job for user in users for job in jobs_of[user.id]})
    [made] = db[CorpusJob.COLLECTION].aggregate([{"$match": {"_id": {"$in": every}}}, {"$facet": facets}])
    return {_uuid.UUID(person): {row["_id"]: fit.shown(row["s"]) for row in rows if row.get("s") is not None}
            for person, rows in made.items()}


# ── what an alert may hold ────────────────────────────────────────────────────────────────────────

def tidy_words(words: list[str]) -> list[str]:
    """Each word as words are compared (`libs.corpus.words.norm`), once, in the order given."""
    return list(dict.fromkeys(cleaned for word in words if (cleaned := norm(word.strip()))))


def tidy_names(names: list[str]) -> list[str]:
    """Each name trimmed, once whatever its case, in the order given."""
    kept: dict[str, str] = {}
    for name in names:
        if cleaned := " ".join(name.split()):
            kept.setdefault(cleaned.casefold(), cleaned)
    return list(kept.values())


def first_alert(db: Database, user_id: _uuid.UUID, channels: list[str] | None = None) -> None:
    """"My search" for an account that has no alert yet: what every account starts with."""
    if not db[Alert.COLLECTION].count_documents({"user_id": user_id}, limit=1):
        insert(db, Alert(user_id=user_id, name=MY_SEARCH, search=True,
                         **({"channels": channels} if channels is not None else {})))


def alerts_for_existing(db: Database) -> int:
    """"My search" for every account from before alerts were named, once ever (its counter, `next_ids`, says
    so after), so nobody's alerts stop: sent where theirs went, the daily email too if it was on (the
    `email_alerts` switch it replaces). How many were made."""
    if next_ids(db, "alerts.for_existing")[0] != 1:
        return 0
    made = 0
    for row in db[User.COLLECTION].find({}, {"email_alerts": 1}):
        if not db[Alert.COLLECTION].count_documents({"user_id": row["_id"]}, limit=1):
            emailed = ["email"] if row.get("email_alerts") else []
            first_alert(db, row["_id"], ["bell", "push", "telegram", *emailed])
            made += 1
    return made


def last_read(db: Database) -> datetime | None:
    """When the alerts last read new jobs for a member: what the Alerts page says it checked."""
    cursor = db[AlertCursor.COLLECTION].find_one({"_id": "0"})
    return cursor["at"] if cursor else None
