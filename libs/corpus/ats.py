"""Shared ATS harvester state: company tokens and conditional-poll ETags."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from pymongo import ReplaceOne, UpdateOne
from pymongo.database import Database

from libs.db import next_ids, now
from libs.db.models import AtsEtag, AtsToken, CompanyLookup


class AtsRepository:
    """Company tokens and ETags for the direct-ATS tier. Shared; takes no `user_id`."""

    def __init__(self, db: Database):
        self._tokens = db[AtsToken.COLLECTION]
        self._etags = db[AtsEtag.COLLECTION]
        self._lookups = db[CompanyLookup.COLLECTION]

    # ── tokens ───────────────────────────────────────────────────────────────────────────────

    def live_tokens(self, ats: str) -> list[str]:
        """Every token still worth polling for this ATS, the one asked longest ago first (never, first)."""
        return [row["token"] for row in self._tokens.find(
            {"ats": ats, "dead_at": None}, {"token": 1},
            sort=[("polled_at", 1), ("first_seen_at", 1), ("token", 1)])]

    def record_asked(self, ats: str, tokens: Iterable[str]) -> None:
        """These boards were asked this run, whatever they answered: the next run asks the others first."""
        if tokens := list(tokens):
            self._tokens.update_many({"ats": ats, "token": {"$in": tokens}}, {"$set": {"polled_at": now()}})

    def add_tokens(self, pairs: Iterable[tuple[str, str]],
                   names: Mapping[tuple[str, str], str] | None = None) -> int:
        """Merge `(ats, token)` pairs, with the company's name where `names` has one (a stored name is
        replaced by a newer one). Returns how many were new."""
        seen: dict[tuple[str, str], str | None] = {}
        for ats, token in pairs:
            ats_n = (ats or "").strip().lower()
            token_n = (token or "").strip().lower()
            if ats_n and token_n:
                seen[(ats_n, token_n)] = (names or {}).get((ats, token))
        if not seen:
            return 0
        result = self._tokens.bulk_write([
            UpdateOne({"ats": ats, "token": token},
                      {"$setOnInsert": {"first_seen_at": now(), "dead_at": None},
                       **({"$set": {"name": name}} if name else {})}, upsert=True)
            for (ats, token), name in sorted(seen.items())], ordered=False)
        return result.upserted_count

    def known_tokens(self) -> set[str]:
        """Every board name stored, on any ATS, dead or alive: a company with one is not looked for."""
        return set(self._tokens.distinct("token"))

    def not_looked_up(self, companies: Iterable[str]) -> list[str]:
        """Of these companies, in their order, the ones never looked for by name (`CompanyLookup`)."""
        companies = list(dict.fromkeys(companies))
        asked = set(self._lookups.distinct("_id", {"_id": {"$in": companies}}))
        return [company for company in companies if company not in asked]

    def looked_up(self, companies: Iterable[str]) -> None:
        """These companies were looked for, a board found or not: not again."""
        writes = [ReplaceOne({"_id": company}, {}, upsert=True) for company in dict.fromkeys(companies)]
        if writes:
            self._lookups.bulk_write(writes, ordered=False)

    def names(self, ats: str) -> dict[str, str]:
        """The companies' own names for this ATS's boards, `{token: name}`, where one is known."""
        return {row["token"]: row["name"] for row in self._tokens.find(
            {"ats": ats, "name": {"$nin": [None, ""]}}, {"token": 1, "name": 1})}

    # ── etags ────────────────────────────────────────────────────────────────────────────────

    def etags(self, ats: str) -> dict[str, str]:
        """Every known ETag for this ATS as `{token: etag}` — the shape the poll loop expects."""
        return {row["token"]: row["etag"] for row in self._etags.find({"ats": ats}, {"token": 1, "etag": 1})}

    def record_polls(self, ats: str, polls: Iterable[tuple[str, str | None, bool]]) -> None:
        """Each board's poll, `(token, etag, alive)`, in a write for all of them rather than one each: one a
        board was 14,000 round trips a run with every board a crawl lists (2026-11-24). A board's validator is
        kept where it sent one (a 304 sends the one it had); not `alive` is a board its ATS said is not there,
        never one that failed to answer or had nothing open (`services.harvester.sources.ats.gather_ats`): its
        validator goes, so one that comes back is read whole, and the board is kept, given up."""
        polls = list(polls)
        if tags := [UpdateOne({"ats": ats, "token": token}, {"$set": {"etag": etag, "updated_at": now()}},
                              upsert=True) for token, etag, alive in polls if alive and etag]:
            self._etags.bulk_write(tags, ordered=False)
        if gone := [token for token, _etag, alive in polls if not alive]:
            self._etags.delete_many({"ats": ats, "token": {"$in": gone}})
            self._tokens.update_many({"ats": ats, "token": {"$in": gone}, "dead_at": None},
                                     {"$set": {"dead_at": now()}})

def revive_all_once(db: Database) -> int:
    """Every board given up before 2026-11-20 asked again, once ever (its counter, `next_ids`, says so
    after); how many. Until then a board that failed to answer one poll, or had nothing open that day, was
    marked dead with the boards that are gone, and nothing tells them apart afterwards. One that is gone
    says so on the next run and is given up again, at the cost of that one request."""
    if next_ids(db, "ats.revive_all_once")[0] != 1:
        return 0
    return db[AtsToken.COLLECTION].update_many({"dead_at": {"$ne": None}},
                                               {"$set": {"dead_at": None}}).modified_count
