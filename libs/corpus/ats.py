"""Shared ATS harvester state: company tokens and conditional-poll ETags."""
from __future__ import annotations

from collections.abc import Iterable

from pymongo import UpdateOne
from pymongo.database import Database

from libs.db import now
from libs.db.models import AtsEtag, AtsToken


class AtsRepository:
    """Company tokens and ETags for the direct-ATS tier. Shared; takes no `user_id`."""

    def __init__(self, db: Database):
        self._tokens = db[AtsToken.COLLECTION]
        self._etags = db[AtsEtag.COLLECTION]

    # ── tokens ───────────────────────────────────────────────────────────────────────────────

    def live_tokens(self, ats: str) -> list[str]:
        """Every token still worth polling for this ATS, oldest first."""
        return [row["token"] for row in self._tokens.find(
            {"ats": ats, "dead_at": None}, {"token": 1}, sort=[("first_seen_at", 1), ("token", 1)])]

    def add_tokens(self, pairs: Iterable[tuple[str, str]]) -> int:
        """Merge `(ats, token)` pairs. Returns how many were new."""
        seen: set[tuple[str, str]] = set()
        for ats, token in pairs:
            ats_n = (ats or "").strip().lower()
            token_n = (token or "").strip().lower()
            if ats_n and token_n:
                seen.add((ats_n, token_n))
        if not seen:
            return 0
        result = self._tokens.bulk_write([
            UpdateOne({"ats": ats, "token": token},
                      {"$setOnInsert": {"first_seen_at": now(), "dead_at": None}}, upsert=True)
            for ats, token in sorted(seen)], ordered=False)
        return result.upserted_count

    def mark_dead(self, ats: str, token: str) -> None:
        """A board that 404s. Kept, not deleted — see `live_tokens`."""
        self._tokens.update_one({"ats": ats, "token": token, "dead_at": None},
                                {"$set": {"dead_at": now()}})

    def revive(self, ats: str, token: str) -> None:
        """A board that answered again after being marked dead."""
        self._tokens.update_one({"ats": ats, "token": token}, {"$set": {"dead_at": None}})

    # ── etags ────────────────────────────────────────────────────────────────────────────────

    def etags(self, ats: str) -> dict[str, str]:
        """Every known ETag for this ATS as `{token: etag}` — the shape the poll loop expects."""
        return {row["token"]: row["etag"] for row in self._etags.find({"ats": ats}, {"token": 1, "etag": 1})}

    def set_etag(self, ats: str, token: str, etag: str) -> None:
        """Record a validator."""
        if not etag:
            return
        self._etags.update_one({"ats": ats, "token": token},
                               {"$set": {"etag": etag, "updated_at": now()}}, upsert=True)

    def forget_etag(self, ats: str, token: str) -> None:
        """Drop a validator for a dead or empty board."""
        self._etags.delete_one({"ats": ats, "token": token})

    def record_poll(self, ats: str, token: str, *, etag: str | None, alive: bool) -> None:
        """Apply one board's poll outcome."""
        if not alive:
            self.forget_etag(ats, token)
            self.mark_dead(ats, token)
            return
        if etag:
            self.set_etag(ats, token, etag)
